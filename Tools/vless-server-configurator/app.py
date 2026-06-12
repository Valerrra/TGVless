from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from deployer import DeploymentError, VlessDeployer
from models import AuthMode, RemoteConfigProbe, TransportType, VlessServerConfig

try:
    import qrcode
    from PIL import ImageTk
except Exception:  # noqa: BLE001
    qrcode = None
    ImageTk = None


APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
STATE_DIR = APP_DIR / "state"
STATE_DIR.mkdir(exist_ok=True)
STATE_FILE = STATE_DIR / "last_profile.json"

BG = "#f4f7fb"
CARD = "#ffffff"
TEXT = "#182533"
MUTED = "#6b7b8c"
ACCENT = "#2aabee"
ACCENT_DARK = "#229ed9"
BORDER = "#dbe5ee"
SUCCESS = "#2ea97d"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Telegram VLESS Configurator")
        self.geometry("1160x800")
        self.minsize(1040, 720)
        self.configure(bg=BG)

        self.current_config: VlessServerConfig | None = None
        self.remote_probe: RemoteConfigProbe | None = None
        self.qr_image = None
        self.wizard_step = 0
        self.context_widget = None

        self.vars = {
            "host": tk.StringVar(),
            "ssh_port": tk.StringVar(value="22"),
            "ssh_username": tk.StringVar(value="root"),
            "ssh_password": tk.StringVar(),
            "ssh_key_path": tk.StringVar(),
            "auth_mode": tk.StringVar(value=AuthMode.PASSWORD.value),
            "listen_port": tk.StringVar(value="443"),
            "transport": tk.StringVar(value=TransportType.TCP.value),
            "uuid": tk.StringVar(value=VlessServerConfig().uuid),
            "server_name": tk.StringVar(),
            "email": tk.StringVar(),
            "ws_path": tk.StringVar(value="/vless"),
            "use_lets_encrypt": tk.BooleanVar(value=False),
            "allow_insecure": tk.BooleanVar(value=True),
            "profile_name": tk.StringVar(value="Telegram VLESS"),
        }

        self._configure_style()
        self._create_context_menu()
        self._build_ui()
        self._bind_clipboard_shortcuts()
        self._bind_dynamic_updates()
        self._load_saved_profile()
        self.show_home()

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background=BG, foreground=TEXT)
        style.configure("Card.TFrame", background=CARD, relief="flat")
        style.configure("App.TFrame", background=BG)
        style.configure("Card.TLabelframe", background=CARD, bordercolor=BORDER, relief="solid")
        style.configure("Card.TLabelframe.Label", background=CARD, foreground=TEXT, font=("Segoe UI Semibold", 11))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 22))
        style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 11))
        style.configure("Section.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI Semibold", 15))
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("Value.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Primary.TButton", background=ACCENT, foreground="white", borderwidth=0, padding=(14, 10))
        style.map("Primary.TButton", background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK)])
        style.configure("Secondary.TButton", background=CARD, foreground=TEXT, borderwidth=1, bordercolor=BORDER, padding=(14, 10))
        style.map("Secondary.TButton", background=[("active", "#eef5fb")])
        style.configure("Pill.TLabel", background="#e8f5fc", foreground=ACCENT_DARK, font=("Segoe UI Semibold", 9), padding=(10, 5))
        style.configure("Accent.TCheckbutton", background=CARD, foreground=TEXT)

    def _create_context_menu(self) -> None:
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Вырезать", command=lambda: self._menu_action("cut"))
        self.context_menu.add_command(label="Копировать", command=lambda: self._menu_action("copy"))
        self.context_menu.add_command(label="Вставить", command=lambda: self._menu_action("paste"))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Выделить всё", command=lambda: self._menu_action("select_all"))

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.root = ttk.Frame(self, style="App.TFrame", padding=18)
        self.root.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.screen = ttk.Frame(self.root, style="App.TFrame")
        self.screen.grid(sticky="nsew")
        self.screen.columnconfigure(0, weight=1)
        self.screen.rowconfigure(0, weight=1)

        self._build_home()
        self._build_wizard()
        self._build_manage()

    def _build_home(self) -> None:
        self.home_frame = ttk.Frame(self.screen, style="App.TFrame")
        self.home_frame.grid(row=0, column=0, sticky="nsew")
        self.home_frame.columnconfigure(0, weight=1)

        hero = ttk.Frame(self.home_frame, style="App.TFrame", padding=(10, 30))
        hero.grid(row=0, column=0, sticky="ew")
        ttk.Label(hero, text="Telegram VLESS Configurator", style="Title.TLabel").pack(anchor="w")

        cards = ttk.Frame(self.home_frame, style="App.TFrame")
        cards.grid(row=1, column=0, sticky="nsew")
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)

        create_card = ttk.Frame(cards, style="Card.TFrame", padding=22)
        create_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ttk.Label(create_card, text="Создать / Найти конфиг", style="Section.TLabel").pack(anchor="w")
        ttk.Button(create_card, text="Далее", style="Primary.TButton", command=self.start_new_config).pack(anchor="w", pady=(18, 0))

        manage_card = ttk.Frame(cards, style="Card.TFrame", padding=22)
        manage_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ttk.Label(manage_card, text="Управлять", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            manage_card,
            text="Перейти к текущему конфигу, выполнить деплой, удалить сервис с VPS, сохранить JSON, URI и QR.",
            style="Muted.TLabel",
            wraplength=360,
            justify="left",
        ).pack(anchor="w", pady=(8, 18))
        ttk.Button(manage_card, text="Открыть", style="Secondary.TButton", command=self.open_manage).pack(anchor="w")

    def _build_wizard(self) -> None:
        self.wizard_frame = ttk.Frame(self.screen, style="App.TFrame")
        self.wizard_frame.grid(row=0, column=0, sticky="nsew")
        self.wizard_frame.columnconfigure(0, weight=1)
        self.wizard_frame.rowconfigure(1, weight=1)

        header = ttk.Frame(self.wizard_frame, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.wizard_header_title = ttk.Label(header, text="Новый конфиг", style="Title.TLabel")
        self.wizard_header_title.pack(anchor="w")
        ttk.Label(header, text="", style="Subtitle.TLabel").pack(anchor="w", pady=(4, 0))

        self.step_pills = ttk.Frame(self.wizard_frame, style="App.TFrame")
        self.step_pills.grid(row=1, column=0, sticky="ew", pady=(0, 12))

        self.wizard_card = ttk.Frame(self.wizard_frame, style="Card.TFrame", padding=22)
        self.wizard_card.grid(row=2, column=0, sticky="nsew")
        self.wizard_card.columnconfigure(0, weight=1)
        self.wizard_card.rowconfigure(1, weight=1)
        self.wizard_title = ttk.Label(self.wizard_card, text="", style="Section.TLabel")
        self.wizard_title.grid(row=0, column=0, sticky="w")
        self.wizard_hint = ttk.Label(self.wizard_card, text="", style="Muted.TLabel", wraplength=720, justify="left")
        self.wizard_hint.grid(row=1, column=0, sticky="w", pady=(6, 10))
        self.wizard_body = ttk.Frame(self.wizard_card, style="Card.TFrame")
        self.wizard_body.grid(row=2, column=0, sticky="nsew")
        self.wizard_body.columnconfigure(1, weight=1)

        nav = ttk.Frame(self.wizard_frame, style="App.TFrame")
        nav.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(nav, text="Назад", style="Secondary.TButton", command=self.prev_step).pack(side="left")
        ttk.Button(nav, text="Отмена", style="Secondary.TButton", command=self.show_home).pack(side="left", padx=(8, 0))
        self.review_actions = ttk.Frame(nav, style="App.TFrame")
        self.review_find_button = ttk.Button(
            self.review_actions, text="Найти конфиг", style="Secondary.TButton", command=self.finish_find_config
        )
        self.review_find_button.pack(side="left")
        self.review_create_button = ttk.Button(
            self.review_actions, text="Создать конфиг", style="Primary.TButton", command=self.finish_create_config
        )
        self.review_create_button.pack(side="left", padx=(8, 0))
        self.next_button = ttk.Button(nav, text="Далее", style="Primary.TButton", command=self.next_step)
        self.next_button.pack(side="right")

    def _build_manage(self) -> None:
        self.manage_frame = ttk.Frame(self.screen, style="App.TFrame")
        self.manage_frame.grid(row=0, column=0, sticky="nsew")
        self.manage_frame.columnconfigure(0, weight=1)
        self.manage_frame.columnconfigure(1, weight=1)
        self.manage_frame.rowconfigure(2, weight=1)

        top = ttk.Frame(self.manage_frame, style="App.TFrame")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Label(top, text="Управление конфигом", style="Title.TLabel").pack(anchor="w")
        ttk.Label(top, text="Деплой, удаление на VPS и экспорт клиентских данных.", style="Subtitle.TLabel").pack(anchor="w", pady=(4, 0))

        summary = ttk.Frame(self.manage_frame, style="Card.TFrame", padding=20)
        summary.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        ttk.Label(summary, text="Текущий конфиг", style="Section.TLabel").pack(anchor="w")
        self.summary_text = tk.Text(summary, height=11, wrap="word", bd=0, bg=CARD, fg=TEXT, font=("Consolas", 10))
        self.summary_text.pack(fill="both", expand=True, pady=(10, 0))

        actions = ttk.Frame(self.manage_frame, style="Card.TFrame", padding=20)
        actions.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        ttk.Label(actions, text="Действия", style="Section.TLabel").pack(anchor="w")
        btns = [
            ("Деплой на VPS", "Primary.TButton", self._start_deploy),
            ("Удалить с VPS", "Secondary.TButton", self._start_delete),
            ("Редактировать", "Secondary.TButton", self.edit_current_config),
            ("Сохранить JSON", "Secondary.TButton", self._save_json),
            ("Сохранить URI", "Secondary.TButton", self._save_uri),
            ("Сохранить QR PNG", "Secondary.TButton", self._save_qr),
            ("Домой", "Secondary.TButton", self.show_home),
        ]
        for text, style_name, command in btns:
            ttk.Button(actions, text=text, style=style_name, command=command).pack(anchor="w", fill="x", pady=(10, 0))

        logs_card = ttk.Frame(self.manage_frame, style="Card.TFrame", padding=20)
        logs_card.grid(row=2, column=0, sticky="nsew", padx=(0, 8))
        logs_card.rowconfigure(1, weight=1)
        logs_card.columnconfigure(0, weight=1)
        ttk.Label(logs_card, text="Логи", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.log_text = tk.Text(logs_card, wrap="word", bd=0, bg=CARD, fg=TEXT, font=("Consolas", 10))
        self.log_text.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

        result_card = ttk.Frame(self.manage_frame, style="Card.TFrame", padding=20)
        result_card.grid(row=2, column=1, sticky="nsew", padx=(8, 0))
        result_card.columnconfigure(0, weight=1)
        result_card.rowconfigure(1, weight=1)
        result_card.rowconfigure(3, weight=1)
        ttk.Label(result_card, text="Client JSON", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.json_text = tk.Text(result_card, wrap="word", height=10, bd=0, bg=CARD, fg=TEXT, font=("Consolas", 10))
        self.json_text.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        uri_header = ttk.Frame(result_card, style="Card.TFrame")
        uri_header.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        uri_header.columnconfigure(0, weight=1)
        ttk.Label(uri_header, text="VLESS URI", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(uri_header, text="Копировать URI", style="Secondary.TButton", command=self._copy_current_uri).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(uri_header, text="Открыть URI", style="Secondary.TButton", command=self._open_current_uri).grid(
            row=0, column=2, padx=(8, 0)
        )
        self.uri_text = tk.Text(result_card, wrap="word", height=4, bd=0, bg=CARD, fg=TEXT, font=("Consolas", 10))
        self.uri_text.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        self.uri_link = tk.Label(
            result_card,
            text="",
            bg=CARD,
            fg=ACCENT_DARK,
            cursor="hand2",
            font=("Segoe UI", 10, "underline"),
            wraplength=460,
            justify="left",
        )
        self.uri_link.grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.uri_link.bind("<Button-1>", lambda _e: self._open_current_uri())
        self.qr_label = ttk.Label(result_card, text="QR появится после деплоя", style="Muted.TLabel")
        self.qr_label.bind("<Button-1>", lambda _e: self._open_qr_preview())
        self.qr_label.grid(row=5, column=0, sticky="n", pady=(14, 0))

    def _bind_dynamic_updates(self) -> None:
        self.vars["host"].trace_add("write", lambda *_: self._update_sni_hint())
        self.vars["server_name"].trace_add("write", lambda *_: self._update_sni_hint())
        self.vars["transport"].trace_add("write", lambda *_: self._toggle_transport())

    def _bind_clipboard_shortcuts(self) -> None:
        self.bind_all("<Control-KeyPress>", self._handle_ctrl_keypress, add="+")
        for sequence in ("<Control-c>", "<Control-C>"):
            self.bind_class("Entry", sequence, self._copy_event)
            self.bind_class("TEntry", sequence, self._copy_event)
            self.bind_class("Text", sequence, self._copy_event)
            self.bind_class("TCombobox", sequence, self._copy_event)
        for sequence in ("<Control-v>", "<Control-V>"):
            self.bind_class("Entry", sequence, self._paste_event)
            self.bind_class("TEntry", sequence, self._paste_event)
            self.bind_class("Text", sequence, self._paste_event)
            self.bind_class("TCombobox", sequence, self._paste_event)
        for sequence in ("<Control-x>", "<Control-X>"):
            self.bind_class("Entry", sequence, self._cut_event)
            self.bind_class("TEntry", sequence, self._cut_event)
            self.bind_class("Text", sequence, self._cut_event)
            self.bind_class("TCombobox", sequence, self._cut_event)
        for sequence in ("<Control-a>", "<Control-A>"):
            self.bind_class("Entry", sequence, self._select_all_event)
            self.bind_class("TEntry", sequence, self._select_all_event)
            self.bind_class("Text", sequence, self._select_all_event)
            self.bind_class("TCombobox", sequence, self._select_all_event)
        for sequence in ("<Button-3>", "<ButtonRelease-3>"):
            self.bind_class("Entry", sequence, self._show_context_menu, add="+")
            self.bind_class("TEntry", sequence, self._show_context_menu, add="+")
            self.bind_class("Text", sequence, self._show_context_menu, add="+")
            self.bind_class("TCombobox", sequence, self._show_context_menu, add="+")

    def _handle_ctrl_keypress(self, event) -> str | None:
        widget = event.widget
        if widget is None:
            return None
        keycode = getattr(event, "keycode", None)
        if keycode in {67, 86, 88, 65}:
            if keycode == 67:
                self._copy_widget(widget)
            elif keycode == 86:
                self._paste_widget(widget)
            elif keycode == 88:
                self._cut_widget(widget)
            else:
                self._select_all_widget(widget)
            return "break"
        char = (event.char or "").lower()
        keysym = (event.keysym or "").lower()
        value = char or keysym
        if value in {"c", "с"}:
            self._copy_widget(widget)
            return "break"
        if value in {"v", "м"}:
            self._paste_widget(widget)
            return "break"
        if value in {"x", "ч"}:
            self._cut_widget(widget)
            return "break"
        if value in {"a", "ф"}:
            self._select_all_widget(widget)
            return "break"
        return None

    def _show_context_menu(self, event) -> str:
        self.context_widget = event.widget
        self.context_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _menu_action(self, action: str) -> None:
        if self.context_widget is None:
            return
        if action == "copy":
            self._copy_widget(self.context_widget)
        elif action == "paste":
            self._paste_widget(self.context_widget)
        elif action == "cut":
            self._cut_widget(self.context_widget)
        elif action == "select_all":
            self._select_all_widget(self.context_widget)

    def _copy_event(self, event) -> str:
        self._copy_widget(event.widget)
        return "break"

    def _paste_event(self, event) -> str:
        self._paste_widget(event.widget)
        return "break"

    def _cut_event(self, event) -> str:
        self._cut_widget(event.widget)
        return "break"

    def _select_all_event(self, event) -> str:
        self._select_all_widget(event.widget)
        return "break"

    def _copy_widget(self, widget) -> None:
        if isinstance(widget, tk.Text):
            try:
                value = widget.get("sel.first", "sel.last")
            except tk.TclError:
                value = widget.get("1.0", "end-1c")
        else:
            try:
                value = widget.selection_get()
            except tk.TclError:
                value = widget.get()
        if value:
            self.clipboard_clear()
            self.clipboard_append(value)
            self.update_idletasks()

    def _paste_widget(self, widget) -> None:
        try:
            value = self.clipboard_get()
        except tk.TclError:
            return
        if isinstance(widget, tk.Text):
            try:
                widget.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
            widget.insert("insert", value)
        else:
            try:
                sel_start = widget.index("sel.first")
                sel_end = widget.index("sel.last")
                widget.delete(sel_start, sel_end)
                widget.insert(sel_start, value)
            except tk.TclError:
                widget.insert("insert", value)

    def _cut_widget(self, widget) -> None:
        self._copy_widget(widget)
        if isinstance(widget, tk.Text):
            try:
                widget.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
        else:
            try:
                sel_start = widget.index("sel.first")
                sel_end = widget.index("sel.last")
                widget.delete(sel_start, sel_end)
            except tk.TclError:
                pass

    def _select_all_widget(self, widget) -> None:
        if isinstance(widget, tk.Text):
            widget.tag_add("sel", "1.0", "end")
            widget.mark_set("insert", "1.0")
            widget.see("insert")
        else:
            widget.selection_range(0, "end")
            widget.icursor("end")

    def start_new_config(self) -> None:
        self._reset_vars()
        self.remote_probe = None
        self.wizard_step = 0
        self._render_step()
        self._show_frame(self.wizard_frame)

    def open_manage(self) -> None:
        if self.current_config is None:
            self._load_saved_profile()
        if self.current_config is None:
            messagebox.showinfo("Нет конфига", "Сначала создай конфиг в wizard.")
            return
        self._refresh_manage()
        self._show_frame(self.manage_frame)

    def edit_current_config(self) -> None:
        if self.current_config is None:
            self.open_manage()
            return
        self._load_config_to_vars(self.current_config)
        self.remote_probe = None
        self.wizard_step = 0
        self._render_step()
        self._show_frame(self.wizard_frame)

    def show_home(self) -> None:
        self._show_frame(self.home_frame)

    def _show_frame(self, frame: ttk.Frame) -> None:
        for child in (self.home_frame, self.wizard_frame, self.manage_frame):
            child.grid_remove()
        frame.grid()

    def prev_step(self) -> None:
        if self.wizard_step > 0:
            self.wizard_step -= 1
            self._render_step()

    def next_step(self) -> None:
        try:
            self._validate_step(self.wizard_step)
        except DeploymentError as exc:
            messagebox.showerror("Ошибка проверки", str(exc))
            return

        if self.wizard_step == 2:
            try:
                self._probe_remote_or_prepare_new()
            except DeploymentError as exc:
                messagebox.showerror("Ошибка сервера", str(exc))
                return

        if self.wizard_step < 3:
            self.wizard_step += 1
            self._render_step()
            return

    def finish_find_config(self) -> None:
        self._finish_wizard(create_mode=False)

    def finish_create_config(self) -> None:
        self._finish_wizard(create_mode=True)

    def _finish_wizard(self, create_mode: bool) -> None:
        try:
            self._validate_step(3)
        except DeploymentError as exc:
            messagebox.showerror("Ошибка проверки", str(exc))
            return
        self._suggest_sni_if_needed()
        self.current_config = self._collect_config()
        self._save_current_profile()
        self._refresh_manage()
        if not create_mode and not (self.remote_probe and self.remote_probe.exists):
            messagebox.showinfo("Конфиг не найден", "На сервере конфиг на этом порту не найден. Открываю экран управления с подготовленными данными.")
        self._show_frame(self.manage_frame)

    def _render_step(self) -> None:
        for child in self.step_pills.winfo_children():
            child.destroy()
        for child in self.wizard_body.winfo_children():
            child.destroy()

        steps = ["Адрес", "SSH", "VLESS", "Проверка"]
        for idx, name in enumerate(steps):
            bg = SUCCESS if idx < self.wizard_step else "#e8f5fc"
            fg = "white" if idx < self.wizard_step else ACCENT_DARK
            if idx == self.wizard_step:
                bg = ACCENT
                fg = "white"
            pill = tk.Label(
                self.step_pills,
                text=f"{idx + 1}. {name}",
                bg=bg,
                fg=fg,
                font=("Segoe UI Semibold", 9),
                padx=12,
                pady=6,
            )
            pill.pack(side="left", padx=(0, 8))

        if self.wizard_step == 3:
            self.next_button.pack_forget()
            self.review_actions.pack(side="right")
        else:
            self.review_actions.pack_forget()
            if not self.next_button.winfo_ismapped():
                self.next_button.pack(side="right")

        if self.wizard_step == 0:
            self.wizard_header_title.config(text="Новый конфиг")
            self.wizard_title.config(text="Введи IP или URL")
            self.wizard_hint.config(text="Если у тебя только IP, позже мы предложим SNI по умолчанию: yandex.ru.")
            self._render_address_step()
            self.next_button.config(text="Далее")
        elif self.wizard_step == 1:
            self.wizard_header_title.config(text="Новый конфиг")
            self.wizard_title.config(text="SSH доступ")
            self.wizard_hint.config(text="Укажи SSH порт, пользователя и способ авторизации.")
            self._render_ssh_step()
            self.next_button.config(text="Далее")
        elif self.wizard_step == 2:
            self.wizard_header_title.config(text="Новый конфиг")
            self.wizard_title.config(text="VLESS и TLS")
            self.wizard_hint.config(
                text="Выбери порт. Если на сервере уже есть VLESS на этом порту, мы подтянем его текущие параметры; если нет, создадим новый."
            )
            self._render_vless_step()
            self.next_button.config(text="Далее")
        else:
            self.wizard_header_title.config(text="Новый конфиг")
            self.wizard_title.config(text="Проверка")
            self.wizard_hint.config(text="Проверь данные и выбери действие.")
            self._render_review_step()

    def _render_address_step(self) -> None:
        self._labeled_entry(self.wizard_body, 0, "Имя профиля", self.vars["profile_name"])
        host_entry = self._labeled_entry(self.wizard_body, 1, "IP или URL сервера", self.vars["host"])
        host_entry.focus_set()
        hint = ttk.Label(
            self.wizard_body,
            text="Можно ввести домен или IP. Адрес подключения всегда берется отсюда.",
            style="Muted.TLabel",
        )
        hint.grid(row=2, column=1, sticky="w", pady=(4, 0))

    def _render_ssh_step(self) -> None:
        self._labeled_entry(self.wizard_body, 0, "SSH Port", self.vars["ssh_port"])
        self._labeled_entry(self.wizard_body, 1, "SSH User", self.vars["ssh_username"])
        ttk.Label(self.wizard_body, text="Авторизация", style="Value.TLabel").grid(row=2, column=0, sticky="w", pady=8)
        auth = ttk.Combobox(
            self.wizard_body,
            state="readonly",
            values=[AuthMode.PASSWORD.value, AuthMode.PRIVATE_KEY.value],
            textvariable=self.vars["auth_mode"],
        )
        auth.grid(row=2, column=1, sticky="ew", pady=8)
        auth.bind("<<ComboboxSelected>>", lambda _e: self._render_step())

        if self.vars["auth_mode"].get() == AuthMode.PASSWORD.value:
            self._labeled_entry(self.wizard_body, 3, "SSH Password", self.vars["ssh_password"], show="*")
        else:
            self._labeled_entry(self.wizard_body, 3, "Key File", self.vars["ssh_key_path"])
            ttk.Button(self.wizard_body, text="Выбрать файл", style="Secondary.TButton", command=self._pick_key_file).grid(
                row=3, column=2, padx=(8, 0), pady=8
            )
            ttk.Label(self.wizard_body, text="Private Key", style="Value.TLabel").grid(row=4, column=0, sticky="nw", pady=8)
            self.key_text = tk.Text(self.wizard_body, height=10, wrap="word", bd=1, relief="solid")
            self.key_text.grid(row=4, column=1, columnspan=2, sticky="ew", pady=8)

    def _render_vless_step(self) -> None:
        self._labeled_entry(self.wizard_body, 0, "VLESS Port", self.vars["listen_port"])

        ttk.Label(self.wizard_body, text="Transport", style="Value.TLabel").grid(row=1, column=0, sticky="w", pady=8)
        transport = ttk.Combobox(
            self.wizard_body,
            state="readonly",
            values=[TransportType.TCP.value, TransportType.WS.value],
            textvariable=self.vars["transport"],
        )
        transport.grid(row=1, column=1, sticky="ew", pady=8)
        transport.bind("<<ComboboxSelected>>", lambda _e: self._render_step())

        hint_text = (
            "UUID вручную не нужен. При повторном входе по SSH в уже созданный на этом порту "
            "VLESS-сервер текущие transport, UUID, TLS и WS path подтянутся автоматически."
        )
        ttk.Label(self.wizard_body, text=hint_text, style="Muted.TLabel", wraplength=560, justify="left").grid(
            row=2, column=1, sticky="w", pady=(2, 0)
        )

        if self.vars["transport"].get() == TransportType.WS.value:
            self._labeled_entry(self.wizard_body, 3, "WS Path", self.vars["ws_path"])
            next_row = 4
        else:
            next_row = 3

        ttk.Checkbutton(
            self.wizard_body,
            text="Выпустить сертификат Let's Encrypt",
            style="Accent.TCheckbutton",
            variable=self.vars["use_lets_encrypt"],
            command=self._render_step,
        ).grid(row=next_row, column=1, sticky="w", pady=(8, 4))

        self._labeled_entry(self.wizard_body, next_row + 1, "SNI / Server Name", self.vars["server_name"])
        sni_hint = ttk.Label(self.wizard_body, text=self._current_sni_hint_text(), style="Muted.TLabel", wraplength=500)
        sni_hint.grid(row=next_row + 2, column=1, sticky="w", pady=(2, 0))

        if self.vars["use_lets_encrypt"].get():
            self._labeled_entry(self.wizard_body, next_row + 3, "Email для Let's Encrypt", self.vars["email"])
        ttk.Checkbutton(
            self.wizard_body,
            text="Разрешить insecure mode для тестирования",
            style="Accent.TCheckbutton",
            variable=self.vars["allow_insecure"],
        ).grid(row=next_row + 4, column=1, sticky="w", pady=(8, 0))

        if self.remote_probe and self.remote_probe.note:
            ttk.Label(self.wizard_body, text=self.remote_probe.note, style="Muted.TLabel", wraplength=560, justify="left").grid(
                row=next_row + 5, column=1, sticky="w", pady=(12, 0)
            )

    def _render_review_step(self) -> None:
        cfg = self._collect_config(preview=True)
        tls_label = "Let's Encrypt" if cfg.use_lets_encrypt else "self-signed"
        summary = [
            f"Профиль: {cfg.profile_name}",
            f"Server: {cfg.host}",
            f"SSH: {cfg.ssh_username}@{cfg.host}:{cfg.ssh_port}",
            f"VLESS port: {cfg.listen_port}",
            f"Transport: {cfg.transport.value}",
            f"SNI: {cfg.effective_sni}",
            f"TLS: {tls_label}",
            f"Insecure: {'yes' if cfg.allow_insecure else 'no'}",
        ]
        review = tk.Text(self.wizard_body, height=12, wrap="word", bd=0, bg=CARD, fg=TEXT, font=("Consolas", 10))
        review.grid(row=0, column=0, columnspan=2, sticky="nsew")
        review.insert("1.0", "\n".join(summary))
        review.configure(state="disabled")

    def _labeled_entry(self, parent, row: int, label: str, variable: tk.StringVar, show: str | None = None):
        ttk.Label(parent, text=label, style="Value.TLabel").grid(row=row, column=0, sticky="w", pady=8)
        entry = ttk.Entry(parent, textvariable=variable, show=show or "")
        entry.grid(row=row, column=1, sticky="ew", pady=8)
        return entry

    def _validate_step(self, step: int) -> None:
        if step == 0:
            if not self.vars["host"].get().strip():
                raise DeploymentError("Укажи IP адрес или URL сервера")
        elif step == 1:
            int(self.vars["ssh_port"].get().strip())
            if not self.vars["ssh_username"].get().strip():
                raise DeploymentError("Укажи SSH user")
            if self.vars["auth_mode"].get() == AuthMode.PASSWORD.value:
                if not self.vars["ssh_password"].get():
                    raise DeploymentError("Укажи SSH пароль")
            else:
                key_text = getattr(self, "key_text", None)
                inline_key = key_text.get("1.0", "end").strip() if key_text and key_text.winfo_exists() else ""
                if not inline_key and not self.vars["ssh_key_path"].get().strip():
                    raise DeploymentError("Укажи SSH ключ: выбери файл или вставь содержимое")
        elif step == 2:
            int(self.vars["listen_port"].get().strip())
            if self.vars["use_lets_encrypt"].get() and not self.vars["email"].get().strip():
                raise DeploymentError("Для Let's Encrypt требуется email")
        else:
            self._collect_config()

    def _is_ip_host(self, value: str) -> bool:
        try:
            ipaddress.ip_address(value.strip())
            return True
        except ValueError:
            return False

    def _suggest_sni_if_needed(self) -> None:
        host = self.vars["host"].get().strip()
        sni = self.vars["server_name"].get().strip()
        if self._is_ip_host(host) and not sni:
            self.vars["server_name"].set("yandex.ru")

    def _current_sni_hint_text(self) -> str:
        host = self.vars["host"].get().strip()
        sni = self.vars["server_name"].get().strip()
        if self._is_ip_host(host) and not sni:
            return "Домен не указан. Для маскировки SNI по умолчанию будет использован yandex.ru."
        if sni:
            return f"Сейчас будет использован SNI: {sni}"
        return "Если укажешь домен, он будет использоваться и как SNI, и для Let's Encrypt."

    def _update_sni_hint(self) -> None:
        if hasattr(self, "wizard_hint") and self.wizard_step == 2:
            self.wizard_hint.config(
                text="Выбери порт. Если на сервере уже есть VLESS на этом порту, мы подтянем его текущие параметры; если нет, создадим новый."
            )

    def _probe_remote_or_prepare_new(self) -> None:
        config = self._collect_config(preview=True)
        probe = VlessDeployer(self.log).probe_remote_config(config)
        self.remote_probe = probe
        if probe.exists:
            self.vars["uuid"].set(probe.uuid or VlessServerConfig().uuid)
            self.vars["transport"].set(probe.transport.value)
            self.vars["ws_path"].set(probe.ws_path or "/vless")
            self.vars["use_lets_encrypt"].set(probe.use_lets_encrypt)
            if probe.server_name:
                self.vars["server_name"].set(probe.server_name)
        else:
            self.vars["uuid"].set(VlessServerConfig().uuid)
            if not self.vars["server_name"].get().strip():
                self._suggest_sni_if_needed()

    def _collect_config(self, preview: bool = False) -> VlessServerConfig:
        key_widget = getattr(self, "key_text", None)
        key_text = key_widget.get("1.0", "end").strip() if key_widget and key_widget.winfo_exists() else ""
        host = self.vars["host"].get().strip()
        if not host:
            raise DeploymentError("Укажи IP или URL сервера")
        server_name = self.vars["server_name"].get().strip()
        if self._is_ip_host(host) and not server_name:
            server_name = "yandex.ru"

        try:
            ssh_port = int(self.vars["ssh_port"].get().strip())
            listen_port = int(self.vars["listen_port"].get().strip())
        except ValueError as exc:
            raise DeploymentError("Порты должны быть числами") from exc

        return VlessServerConfig(
            host=host,
            ssh_port=ssh_port,
            ssh_username=self.vars["ssh_username"].get().strip(),
            ssh_password=self.vars["ssh_password"].get(),
            ssh_private_key=key_text,
            ssh_key_path=self.vars["ssh_key_path"].get().strip(),
            auth_mode=AuthMode(self.vars["auth_mode"].get()),
            listen_port=listen_port,
            transport=TransportType(self.vars["transport"].get()),
            uuid=self.vars["uuid"].get().strip() or VlessServerConfig().uuid,
            server_name=server_name,
            email=self.vars["email"].get().strip(),
            ws_path=self.vars["ws_path"].get().strip(),
            use_lets_encrypt=self.vars["use_lets_encrypt"].get(),
            allow_insecure=self.vars["allow_insecure"].get(),
            profile_name=self.vars["profile_name"].get().strip() or "Telegram VLESS",
        )

    def _refresh_manage(self) -> None:
        if self.current_config is None:
            return
        tls_label = "Let's Encrypt" if self.current_config.use_lets_encrypt else "self-signed"
        summary = [
            f"Профиль: {self.current_config.profile_name}",
            f"Server: {self.current_config.host}",
            f"SSH: {self.current_config.ssh_username}@{self.current_config.host}:{self.current_config.ssh_port}",
            f"Port: {self.current_config.listen_port}",
            f"Transport: {self.current_config.transport.value}",
            f"SNI: {self.current_config.effective_sni}",
            f"TLS: {tls_label}",
            f"WS Path: {self.current_config.normalized_ws_path if self.current_config.transport == TransportType.WS else '-'}",
        ]
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", "\n".join(summary))
        self.summary_text.configure(state="disabled")
        self._populate_client_outputs()

    def _populate_client_outputs(self) -> None:
        if self.current_config is None:
            self.json_text.delete("1.0", "end")
            self.uri_text.delete("1.0", "end")
            self.uri_link.configure(text="")
            self.qr_label.configure(image="", text="QR появится после выбора конфига")
            self.qr_image = None
            return
        client_json = self.current_config.client_json_string()
        client_uri = self.current_config.client_uri()
        self.json_text.delete("1.0", "end")
        self.json_text.insert("1.0", client_json)
        self.uri_text.delete("1.0", "end")
        self.uri_text.insert("1.0", client_uri)
        self.uri_link.configure(text=client_uri)
        self._render_qr(client_uri)
        self._autosave_outputs(client_json, client_uri)

    def _autosave_outputs(self, client_json: str, client_uri: str) -> None:
        if self.current_config is None:
            return
        base_name = self._artifact_base_name()
        json_path = OUTPUT_DIR / f"{base_name}.json"
        uri_path = OUTPUT_DIR / f"{base_name}.txt"
        json_path.write_text(client_json, encoding="utf-8")
        uri_path.write_text(client_uri, encoding="utf-8")
        if qrcode is not None:
            qr_path = OUTPUT_DIR / f"{base_name}.png"
            qrcode.make(client_uri).save(qr_path)
            self.log(f"Автосейв: {json_path.name}, {uri_path.name}, {qr_path.name}")
        else:
            self.log(f"Автосейв: {json_path.name}, {uri_path.name}")

    def _artifact_base_name(self) -> str:
        if self.current_config is None:
            return "vless-config"
        raw_name = self.current_config.profile_name.strip() or "vless-config"
        safe_name = re.sub(r"[^0-9A-Za-zА-Яа-я._-]+", "_", raw_name).strip("._-") or "vless-config"
        return f"{safe_name}_{self.current_config.listen_port}"

    def _reset_vars(self) -> None:
        self.vars["host"].set("")
        self.vars["ssh_port"].set("22")
        self.vars["ssh_username"].set("root")
        self.vars["ssh_password"].set("")
        self.vars["ssh_key_path"].set("")
        self.vars["auth_mode"].set(AuthMode.PASSWORD.value)
        self.vars["listen_port"].set("443")
        self.vars["transport"].set(TransportType.TCP.value)
        self.vars["uuid"].set(VlessServerConfig().uuid)
        self.vars["server_name"].set("")
        self.vars["email"].set("")
        self.vars["ws_path"].set("/vless")
        self.vars["use_lets_encrypt"].set(False)
        self.vars["allow_insecure"].set(True)
        self.vars["profile_name"].set("Telegram VLESS")
        self.remote_probe = None

    def _load_config_to_vars(self, config: VlessServerConfig) -> None:
        self.vars["host"].set(config.host)
        self.vars["ssh_port"].set(str(config.ssh_port))
        self.vars["ssh_username"].set(config.ssh_username)
        self.vars["ssh_password"].set(config.ssh_password)
        self.vars["ssh_key_path"].set(config.ssh_key_path)
        self.vars["auth_mode"].set(config.auth_mode.value)
        self.vars["listen_port"].set(str(config.listen_port))
        self.vars["transport"].set(config.transport.value)
        self.vars["uuid"].set(config.uuid)
        self.vars["server_name"].set(config.server_name)
        self.vars["email"].set(config.email)
        self.vars["ws_path"].set(config.ws_path)
        self.vars["use_lets_encrypt"].set(config.use_lets_encrypt)
        self.vars["allow_insecure"].set(config.allow_insecure)
        self.vars["profile_name"].set(config.profile_name)
        if hasattr(self, "key_text") and self.key_text.winfo_exists():
            self.key_text.delete("1.0", "end")
            if config.ssh_private_key:
                self.key_text.insert("1.0", config.ssh_private_key)

    def _save_current_profile(self) -> None:
        if self.current_config is None:
            return
        STATE_FILE.write_text(
            json.dumps(self.current_config.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_saved_profile(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.current_config = VlessServerConfig.from_dict(data)
        except Exception:
            self.current_config = None

    def _pick_key_file(self) -> None:
        path = filedialog.askopenfilename(title="Выбери приватный SSH ключ")
        if path:
            self.vars["ssh_key_path"].set(path)

    def _start_deploy(self) -> None:
        try:
            config = self.current_config or self._collect_config()
            self.current_config = config
            self._save_current_profile()
        except DeploymentError as exc:
            messagebox.showerror("Ошибка", str(exc))
            return
        threading.Thread(target=self._deploy_worker, args=(config,), daemon=True).start()

    def _start_delete(self) -> None:
        try:
            config = self.current_config or self._collect_config()
        except DeploymentError as exc:
            messagebox.showerror("Ошибка", str(exc))
            return
        if not messagebox.askyesno("Удаление", "Удалить VLESS-конфиг и service с сервера?"):
            return
        threading.Thread(target=self._delete_worker, args=(config,), daemon=True).start()

    def _deploy_worker(self, config: VlessServerConfig) -> None:
        deployer = VlessDeployer(self.log)
        try:
            client_json, client_uri = deployer.deploy(config)
            self.after(0, lambda: self._show_result(client_json, client_uri))
        except Exception as exc:  # noqa: BLE001
            self.log(f"VLESS DISCONNECTED: {exc}")
            self.after(0, lambda: messagebox.showerror("Deploy error", str(exc)))

    def _delete_worker(self, config: VlessServerConfig) -> None:
        deployer = VlessDeployer(self.log)
        try:
            deployer.delete_remote_config(config)
            self.after(0, lambda: messagebox.showinfo("Удаление", "Конфиг на сервере удален"))
        except Exception as exc:  # noqa: BLE001
            self.log(f"VLESS DISCONNECTED: {exc}")
            self.after(0, lambda: messagebox.showerror("Delete error", str(exc)))

    def _show_result(self, client_json: str, client_uri: str) -> None:
        self.json_text.delete("1.0", "end")
        self.json_text.insert("1.0", client_json)
        self.uri_text.delete("1.0", "end")
        self.uri_text.insert("1.0", client_uri)
        self.uri_link.configure(text=client_uri)
        self._render_qr(client_uri)
        self._autosave_outputs(client_json, client_uri)
        self._refresh_manage()

    def _render_qr(self, text: str) -> None:
        if qrcode is None or ImageTk is None:
            self.qr_label.configure(text="QR недоступен: установи qrcode[pil]")
            return
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, border=6, box_size=12)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((320, 320))
        self.qr_image = ImageTk.PhotoImage(img)
        self.qr_label.configure(image=self.qr_image, text="")

    def _get_current_uri(self) -> str:
        return self.uri_text.get("1.0", "end").strip()

    def _copy_current_uri(self) -> None:
        uri = self._get_current_uri()
        if not uri:
            messagebox.showwarning("URI", "URI пока пустой")
            return
        self.clipboard_clear()
        self.clipboard_append(uri)
        self.update_idletasks()
        self.log("URI скопирован в буфер обмена")

    def _open_current_uri(self) -> None:
        uri = self._get_current_uri()
        if not uri:
            messagebox.showwarning("URI", "URI пока пустой")
            return
        try:
            webbrowser.open(uri)
            self.log("Открыт системный обработчик URI")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Открытие URI", str(exc))

    def _open_qr_preview(self) -> None:
        uri = self._get_current_uri()
        if not uri or qrcode is None or ImageTk is None:
            return
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, border=8, box_size=14)
        qr.add_data(uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        preview = tk.Toplevel(self)
        preview.title("QR Preview")
        preview.configure(bg="white")
        preview.resizable(False, False)
        big_image = ImageTk.PhotoImage(img)
        label = tk.Label(preview, image=big_image, bg="white")
        label.image = big_image
        label.pack(padx=18, pady=18)
        tk.Label(preview, text="Нажми по URI выше или скопируй его отдельно, если сканер не открывает схему.", bg="white", fg=MUTED).pack(
            padx=18, pady=(0, 18)
        )

    def _save_json(self) -> None:
        source = self.json_text.get("1.0", "end").strip()
        if not source:
            if self.current_config is None:
                messagebox.showerror("Ошибка", "Нет конфига для экспорта")
                return
            source = self.current_config.client_json_string()
        path = filedialog.asksaveasfilename(
            title="Сохранить JSON",
            defaultextension=".json",
            initialdir=str(OUTPUT_DIR),
            filetypes=[("JSON", "*.json")],
        )
        if path:
            Path(path).write_text(source, encoding="utf-8")
            self.log(f"JSON сохранен: {path}")

    def _save_uri(self) -> None:
        source = self._get_current_uri()
        if not source:
            if self.current_config is None:
                messagebox.showerror("Ошибка", "Нет конфига для экспорта")
                return
            source = self.current_config.client_uri()
        path = filedialog.asksaveasfilename(
            title="Сохранить URI",
            defaultextension=".txt",
            initialdir=str(OUTPUT_DIR),
            filetypes=[("Text", "*.txt")],
        )
        if path:
            Path(path).write_text(source, encoding="utf-8")
            self.log(f"URI сохранен: {path}")

    def _save_qr(self) -> None:
        if qrcode is None:
            messagebox.showerror("Ошибка", "Не установлен пакет qrcode[pil]")
            return
        source = self._get_current_uri()
        if not source:
            if self.current_config is None:
                messagebox.showerror("Ошибка", "Нет URI для QR")
                return
            source = self.current_config.client_uri()
        path = filedialog.asksaveasfilename(
            title="Сохранить QR",
            defaultextension=".png",
            initialdir=str(OUTPUT_DIR),
            filetypes=[("PNG", "*.png")],
        )
        if path:
            qrcode.make(source).save(path)
            self.log(f"QR сохранен: {path}")

    def log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        self.after(0, lambda: self._append_log(line))

    def _append_log(self, line: str) -> None:
        self.log_text.insert("end", line)
        self.log_text.see("end")


if __name__ == "__main__":
    App().mainloop()
