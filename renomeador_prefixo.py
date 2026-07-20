#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Renomeador de arquivos com prefixo, pré-visualização e desfazer seguro."""

from __future__ import annotations

import ctypes
import json
import os
import sys
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from renomeador_core import (
    PersistenciaErro,
    RenomeadorService,
    executar_autoteste_core,
    listar_arquivos,
    montar_preview,
    validar_prefixo,
)

APP_VERSION = "2.1.1"
MAX_HISTORICO = 8
MAX_LINHAS_PREVIEW = 500
CONFIG_DIR = Path.home() / ".renomeador_arquivos"
CONFIG_FILE = CONFIG_DIR / "config.json"

FONTE_TITULO = ("Segoe UI", 20, "bold")
FONTE_SUBTITULO = ("Segoe UI", 11)
FONTE_NORMAL = ("Segoe UI", 12)
FONTE_PEQUENA = ("Segoe UI", 10)

TEMA_CLARO = {
    "bg": "#f3f3f3",
    "surface": "#ffffff",
    "field": "#ffffff",
    "fg": "#1b1b1b",
    "muted": "#616161",
    "border": "#dedede",
    "accent": "#0067c0",
    "accent_hover": "#005fb8",
    "disabled": "#a6a6a6",
    "conflict": "#c42b1c",
    "warning": "#9a6700",
    "button": "#f6f6f6",
    "button_hover": "#eaeaea",
}

MAPA_EXTENSOES = {
    "PDF": [".pdf"],
    "EPS": [".eps"],
    "TIFF": [".tif", ".tiff"],
    "PNG": [".png"],
    "JPG": [".jpg", ".jpeg"],
}
ORDEM_TIPOS = ["PDF", "EPS", "TIFF", "PNG", "JPG"]


def _configurar_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _salvar_config_atomico(dados: object) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temporario = CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.tmp-{os.getpid()}")
    with temporario.open("w", encoding="utf-8", newline="\n") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2)
        arquivo.flush()
        os.fsync(arquivo.fileno())
    os.replace(temporario, CONFIG_FILE)


class RenomeadorApp:
    def __init__(self, root: ctk.CTk):
        self.root = root
        # Não ocultar a janela principal durante a inicialização. Em alguns builds
        # windowed do PyInstaller, withdraw/deiconify podia deixá-la invisível.
        self.root.title(f"Renomeador de Arquivos — v{APP_VERSION}")
        self.root.minsize(760, 650)
        self.root.protocol("WM_DELETE_WINDOW", self._fechar)

        self.cores = TEMA_CLARO
        self.pasta_selecionada = tk.StringVar(value="")
        self.prefixo = tk.StringVar(value="")
        self.var_todos = tk.BooleanVar(value=True)
        self.vars_tipos = {tipo: tk.BooleanVar(value=False) for tipo in ORDEM_TIPOS}

        self.arquivos_na_pasta: list[str] = []
        self.total_arquivos = 0
        self.historico_prefixos = self._carregar_historico()
        self.log_pasta_atual: list[dict[str, object]] = []
        self.service: RenomeadorService | None = None
        self.checks_tipos: list[ctk.CTkCheckBox] = []
        self.em_processamento = False

        self._aplicar_tema()
        self._montar_interface()
        self._ajustar_tamanho_inicial()
        self.root.bind("<Return>", self._ao_pressionar_enter)
        self.root.after_idle(self._exibir_janela_principal)

    # ------------------------------------------------------------------
    # Configuração
    # ------------------------------------------------------------------
    def _carregar_historico(self) -> list[str]:
        try:
            if CONFIG_FILE.exists():
                dados = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                itens = dados.get("prefixos_recentes", [])
                if isinstance(itens, list):
                    return [str(item).strip() for item in itens if str(item).strip()][:MAX_HISTORICO]
        except Exception:
            pass
        return []

    def _salvar_historico(self) -> None:
        try:
            _salvar_config_atomico({"prefixos_recentes": self.historico_prefixos})
        except Exception:
            # O histórico é conveniência; falhar aqui não afeta a segurança dos arquivos.
            pass

    def _registrar_prefixo_usado(self, prefixo: str) -> None:
        prefixo = prefixo.strip()
        if not prefixo:
            return
        self.historico_prefixos = [
            item for item in self.historico_prefixos if item.casefold() != prefixo.casefold()
        ]
        self.historico_prefixos.insert(0, prefixo)
        self.historico_prefixos = self.historico_prefixos[:MAX_HISTORICO]
        self._salvar_historico()
        self.combo_prefixo.configure(values=self.historico_prefixos or [""])

    # ------------------------------------------------------------------
    # Janela e tema
    # ------------------------------------------------------------------
    def _aplicar_tema(self) -> None:
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.root.configure(fg_color=self.cores["bg"])

    def _centralizar_janela(self, janela: ctk.CTk | ctk.CTkToplevel | None = None) -> None:
        janela = janela or self.root
        janela.update_idletasks()
        largura = janela.winfo_width()
        altura = janela.winfo_height()
        x = max((janela.winfo_screenwidth() - largura) // 2, 0)
        y = max((janela.winfo_screenheight() - altura) // 2, 0)
        janela.geometry(f"{largura}x{altura}+{x}+{y}")

    def _ajustar_tamanho_inicial(self) -> None:
        try:
            self.root.update_idletasks()
            largura_tela = self.root.winfo_screenwidth()
            altura_tela = self.root.winfo_screenheight()
            largura = max(760, min(self.root.winfo_reqwidth(), largura_tela - 80))
            altura = max(650, min(self.root.winfo_reqheight(), altura_tela - 100))
            x = max((largura_tela - largura) // 2, 0)
            y = max((altura_tela - altura) // 2, 0)
            self.root.geometry(f"{largura}x{altura}+{x}+{y}")
        except Exception:
            self.root.geometry("900x720")
            self._centralizar_janela()

    def _exibir_janela_principal(self) -> None:
        """Garante que a janela fique visível e em primeiro plano após o primeiro ciclo do Tk."""
        try:
            self.root.update_idletasks()
            self.root.state("normal")
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(180, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except tk.TclError:
            pass

    def _fechar(self) -> None:
        if self.em_processamento:
            messagebox.showwarning(
                "Operação em andamento",
                "Aguarde a conclusão da operação antes de fechar o aplicativo.",
            )
            return
        self.root.destroy()

    def _ao_pressionar_enter(self, _evento: object = None) -> None:
        if not self.em_processamento and self.pasta_selecionada.get():
            self.iniciar_renomeacao()

    # ------------------------------------------------------------------
    # Interface
    # ------------------------------------------------------------------
    def _montar_interface(self) -> None:
        c = self.cores
        container = ctk.CTkFrame(self.root, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=24, pady=18)

        header = ctk.CTkFrame(container, fg_color="transparent")
        header.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(
            header, text="Renomeador de Arquivos", font=FONTE_TITULO, text_color=c["fg"]
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Adicione um prefixo com pré-visualização, proteção contra conflitos e desfazer.",
            font=FONTE_SUBTITULO,
            text_color=c["muted"],
        ).pack(anchor="w")

        card_pasta = self._criar_card(container, "1.  Pasta")
        linha_pasta = ctk.CTkFrame(card_pasta, fg_color="transparent")
        linha_pasta.pack(fill="x", padx=16, pady=(8, 0))
        self.entry_pasta = ctk.CTkEntry(
            linha_pasta,
            textvariable=self.pasta_selecionada,
            height=38,
            font=FONTE_NORMAL,
            state="disabled",
            fg_color=c["field"],
            border_color=c["border"],
            text_color=c["fg"],
        )
        self.entry_pasta.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.btn_procurar = ctk.CTkButton(
            linha_pasta,
            text="Procurar...",
            width=108,
            height=38,
            command=self.selecionar_pasta,
            font=FONTE_PEQUENA,
        )
        self.btn_procurar.pack(side="left")
        self.label_contagem = ctk.CTkLabel(
            card_pasta,
            text="Nenhuma pasta selecionada.",
            font=FONTE_PEQUENA,
            text_color=c["muted"],
        )
        self.label_contagem.pack(anchor="w", padx=16, pady=(8, 1))
        self.label_desfazer_info = ctk.CTkLabel(
            card_pasta, text="", font=FONTE_PEQUENA, text_color=c["accent"]
        )
        self.label_desfazer_info.pack(anchor="w", padx=16, pady=(0, 10))

        card_prefixo = self._criar_card(container, "2.  Prefixo")
        linha_prefixo = ctk.CTkFrame(card_prefixo, fg_color="transparent")
        linha_prefixo.pack(fill="x", padx=16, pady=(8, 0))
        ctk.CTkLabel(
            linha_prefixo,
            text="Prefixo",
            font=FONTE_NORMAL,
            text_color=c["fg"],
            width=65,
        ).pack(side="left", padx=(0, 10))
        self.combo_prefixo = ctk.CTkComboBox(
            linha_prefixo,
            variable=self.prefixo,
            values=self.historico_prefixos or [""],
            height=38,
            font=FONTE_NORMAL,
            fg_color=c["field"],
            border_color=c["border"],
            text_color=c["fg"],
            button_color=c["button"],
            button_hover_color=c["button_hover"],
        )
        self.combo_prefixo.pack(side="left", fill="x", expand=True)
        self.label_exemplo = ctk.CTkLabel(
            card_prefixo,
            text="Exemplo: PREFIXO _ nome_do_arquivo.extensao",
            font=FONTE_PEQUENA,
            text_color=c["muted"],
        )
        self.label_exemplo.pack(anchor="w", padx=16, pady=(8, 0))
        self.label_aviso_prefixo = ctk.CTkLabel(
            card_prefixo, text="", font=FONTE_PEQUENA, text_color=c["conflict"]
        )
        self.label_aviso_prefixo.pack(anchor="w", padx=16, pady=(0, 10))
        self.prefixo.trace_add("write", lambda *_: self._atualizar_exemplo())

        card_filtro = self._criar_card(container, "3.  Tipos de arquivo (opcional)")
        linha_filtro = ctk.CTkFrame(card_filtro, fg_color="transparent")
        linha_filtro.pack(fill="x", padx=16, pady=(8, 0))
        self.chk_todos = ctk.CTkCheckBox(
            linha_filtro,
            text="Todos",
            variable=self.var_todos,
            command=self._ao_marcar_todos,
            font=FONTE_PEQUENA,
        )
        self.chk_todos.pack(side="left", padx=(0, 18))
        for tipo in ORDEM_TIPOS:
            check = ctk.CTkCheckBox(
                linha_filtro,
                text=tipo,
                variable=self.vars_tipos[tipo],
                command=self._ao_marcar_tipo,
                font=FONTE_PEQUENA,
                state="disabled",
            )
            check.pack(side="left", padx=(0, 15))
            self.checks_tipos.append(check)
        ctk.CTkLabel(
            card_filtro,
            text="Marque tipos específicos ou mantenha “Todos”. Pastas internas não são processadas.",
            font=FONTE_PEQUENA,
            text_color=c["muted"],
        ).pack(anchor="w", padx=16, pady=(8, 10))

        card_progresso = self._criar_card(container, "4.  Progresso")
        self.progress_bar = ctk.CTkProgressBar(card_progresso, height=10)
        self.progress_bar.pack(fill="x", padx=16, pady=(10, 6))
        self.progress_bar.set(0)
        self.label_status = ctk.CTkLabel(
            card_progresso,
            text="Pronto para iniciar.",
            font=FONTE_PEQUENA,
            text_color=c["muted"],
        )
        self.label_status.pack(anchor="w", padx=16, pady=(0, 10))

        botoes = ctk.CTkFrame(container, fg_color="transparent")
        botoes.pack(fill="x")
        self.btn_executar = ctk.CTkButton(
            botoes,
            text="Renomear Arquivos",
            command=self.iniciar_renomeacao,
            height=38,
            font=FONTE_PEQUENA,
            state="disabled",
        )
        self.btn_executar.pack(side="left")
        self.btn_desfazer = ctk.CTkButton(
            botoes,
            text="Desfazer última ação",
            command=self.desfazer_ultima_operacao,
            height=38,
            font=FONTE_PEQUENA,
            fg_color=c["button"],
            hover_color=c["button_hover"],
            text_color=c["fg"],
            state="disabled",
        )
        self.btn_desfazer.pack(side="left", padx=10)
        ctk.CTkLabel(
            botoes,
            text=f"Versão {APP_VERSION}",
            font=FONTE_PEQUENA,
            text_color=c["disabled"],
        ).pack(side="right")

        self.pasta_selecionada.trace_add("write", lambda *_: self._atualizar_estado_acoes())

    def _criar_card(self, pai: ctk.CTkFrame, titulo: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            pai,
            fg_color=self.cores["surface"],
            border_width=1,
            border_color=self.cores["border"],
            corner_radius=10,
        )
        card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            card,
            text=titulo,
            font=("Segoe UI", 11, "bold"),
            text_color=self.cores["fg"],
        ).pack(anchor="w", padx=16, pady=(10, 0))
        return card

    def _atualizar_exemplo(self) -> None:
        prefixo = self.prefixo.get().strip()
        self.label_exemplo.configure(
            text=(
                f"Exemplo: {prefixo} _ nome_do_arquivo.extensao"
                if prefixo
                else "Exemplo: PREFIXO _ nome_do_arquivo.extensao"
            )
        )
        valido, mensagem = validar_prefixo(prefixo) if prefixo else (True, "")
        self.label_aviso_prefixo.configure(text="" if valido else mensagem.replace("\n", " "))

    def _atualizar_estado_acoes(self) -> None:
        tem_pasta = bool(self.pasta_selecionada.get())
        self.btn_executar.configure(state="normal" if tem_pasta and not self.em_processamento else "disabled")
        self._atualizar_estado_desfazer()

    def _atualizar_estado_desfazer(self) -> None:
        if self.log_pasta_atual:
            ultima = self.log_pasta_atual[-1]
            itens = ultima.get("itens", [])
            quantidade = len(itens) if isinstance(itens, list) else 0
            data = str(ultima.get("data_exibicao", ""))
            self.label_desfazer_info.configure(
                text=f"Última ação nesta pasta: {quantidade} arquivo(s) em {data}"
            )
            estado = "disabled" if self.em_processamento else "normal"
            self.btn_desfazer.configure(state=estado)
        else:
            self.label_desfazer_info.configure(text="")
            self.btn_desfazer.configure(state="disabled")

    def _set_processando(self, ativo: bool, texto: str = "") -> None:
        self.em_processamento = ativo
        estado = "disabled" if ativo else "normal"
        self.btn_procurar.configure(state=estado)
        self.combo_prefixo.configure(state=estado)
        self.chk_todos.configure(state=estado)
        for check in self.checks_tipos:
            if ativo:
                check.configure(state="disabled")
            else:
                check.configure(state="disabled" if self.var_todos.get() else "normal")
        self.label_status.configure(text=texto or ("Processando..." if ativo else "Pronto para iniciar."))
        self._atualizar_estado_acoes()
        self.root.update_idletasks()

    # ------------------------------------------------------------------
    # Filtros
    # ------------------------------------------------------------------
    def _ao_marcar_todos(self) -> None:
        if self.var_todos.get():
            for variavel in self.vars_tipos.values():
                variavel.set(False)
            for check in self.checks_tipos:
                check.configure(state="disabled")
        else:
            for check in self.checks_tipos:
                check.configure(state="normal")

    def _ao_marcar_tipo(self) -> None:
        if any(variavel.get() for variavel in self.vars_tipos.values()):
            self.var_todos.set(False)
            for check in self.checks_tipos:
                check.configure(state="normal")
        else:
            self.var_todos.set(True)
            for check in self.checks_tipos:
                check.configure(state="disabled")

    def _obter_filtro_extensoes(self) -> set[str] | None:
        if self.var_todos.get():
            return None
        extensoes: set[str] = set()
        for tipo, variavel in self.vars_tipos.items():
            if variavel.get():
                extensoes.update(MAPA_EXTENSOES[tipo])
        return extensoes

    # ------------------------------------------------------------------
    # Pasta e histórico
    # ------------------------------------------------------------------
    def selecionar_pasta(self) -> None:
        pasta = filedialog.askdirectory(title="Selecione a pasta com os arquivos")
        if not pasta:
            return
        self.pasta_selecionada.set(pasta)
        if not self._contar_arquivos(pasta):
            self.pasta_selecionada.set("")
            return

        self.service = RenomeadorService(pasta)
        self.log_pasta_atual, avisos = self.service.carregar_estado()
        self._atualizar_estado_desfazer()
        if avisos:
            messagebox.showwarning("Recuperação de segurança", "\n\n".join(avisos))

    def _contar_arquivos(self, pasta: str) -> bool:
        self.arquivos_na_pasta = []
        self.total_arquivos = 0
        try:
            arquivos = listar_arquivos(pasta, executavel_atual=sys.argv[0])
        except Exception as exc:
            self.label_contagem.configure(text="Não foi possível ler a pasta.")
            messagebox.showerror("Erro", f"Não foi possível ler a pasta:\n{exc}")
            return False

        self.arquivos_na_pasta = arquivos
        self.total_arquivos = len(arquivos)
        self.label_contagem.configure(text=f"A pasta contém {self.total_arquivos} arquivo(s).")
        return True

    # ------------------------------------------------------------------
    # Pré-visualização e execução
    # ------------------------------------------------------------------
    def iniciar_renomeacao(self) -> None:
        pasta = self.pasta_selecionada.get()
        prefixo = self.prefixo.get().strip()
        if not pasta:
            messagebox.showwarning("Atenção", "Selecione uma pasta antes de continuar.")
            return

        valido, mensagem = validar_prefixo(prefixo)
        if not valido:
            messagebox.showwarning("Prefixo inválido", mensagem)
            return

        if not self._contar_arquivos(pasta):
            return
        if not self.arquivos_na_pasta:
            messagebox.showinfo("Aviso", "Não há arquivos nesta pasta para renomear.")
            return

        filtro = self._obter_filtro_extensoes()
        if filtro is None:
            considerados = self.arquivos_na_pasta
        elif not filtro:
            messagebox.showinfo("Aviso", "Selecione pelo menos um tipo de arquivo.")
            return
        else:
            considerados = [
                nome for nome in self.arquivos_na_pasta if Path(nome).suffix.casefold() in filtro
            ]

        if not considerados:
            messagebox.showinfo("Aviso", "Nenhum arquivo corresponde ao tipo selecionado.")
            return

        prefixo_formatado = f"{prefixo} _ "
        try:
            resultados = montar_preview(pasta, prefixo_formatado, considerados)
        except Exception as exc:
            messagebox.showerror("Erro", f"Não foi possível preparar a pré-visualização:\n{exc}")
            return

        self._abrir_janela_preview(pasta, prefixo, prefixo_formatado, resultados)

    def _abrir_janela_preview(
        self,
        pasta: str,
        prefixo: str,
        prefixo_formatado: str,
        resultados: list[dict[str, str]],
    ) -> None:
        n_renomear = sum(item["status"] == "renomear" for item in resultados)
        n_ignorados = sum(item["status"] == "ignorado" for item in resultados)
        n_conflitos = len(resultados) - n_renomear - n_ignorados

        janela = ctk.CTkToplevel(self.root, fg_color=self.cores["bg"])
        janela.withdraw()
        janela.title("Pré-visualização da Renomeação")
        largura = max(720, min(980, janela.winfo_screenwidth() - 80))
        altura = max(480, min(650, janela.winfo_screenheight() - 100))
        janela.geometry(f"{largura}x{altura}")
        janela.minsize(720, 480)
        janela.transient(self.root)
        janela.grab_set()

        frame = ctk.CTkFrame(janela, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=24, pady=22)

        topo = ctk.CTkFrame(frame, fg_color="transparent")
        topo.pack(fill="x")
        ctk.CTkLabel(
            topo,
            text="Confira as alterações antes de confirmar",
            font=("Segoe UI", 15, "bold"),
            text_color=self.cores["fg"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            topo,
            text=(
                f"{n_renomear} serão renomeado(s)  •  {n_ignorados} já possuem o prefixo  •  "
                f"{n_conflitos} não serão alterado(s)"
            ),
            font=FONTE_PEQUENA,
            text_color=self.cores["muted"],
        ).pack(anchor="w", pady=(4, 12))

        botoes = ctk.CTkFrame(frame, fg_color="transparent")
        botoes.pack(side="bottom", fill="x", pady=(16, 0))

        def cancelar() -> None:
            janela.destroy()

        def confirmar() -> None:
            janela.destroy()
            self._executar_renomeacao(pasta, prefixo, prefixo_formatado, resultados)

        janela.bind("<Escape>", lambda _e: cancelar())
        if n_renomear == 0:
            ctk.CTkLabel(
                botoes,
                text="Nenhum arquivo será alterado.",
                font=FONTE_PEQUENA,
                text_color=self.cores["muted"],
            ).pack(side="left")
        ctk.CTkButton(
            botoes,
            text="Cancelar",
            command=cancelar,
            width=100,
            fg_color=self.cores["button"],
            hover_color=self.cores["button_hover"],
            text_color=self.cores["fg"],
        ).pack(side="right")
        btn_confirmar = ctk.CTkButton(
            botoes,
            text=f"Confirmar e Renomear ({n_renomear})",
            command=confirmar,
        )
        btn_confirmar.pack(side="right", padx=(0, 10))
        if n_renomear == 0:
            btn_confirmar.configure(state="disabled")
        else:
            btn_confirmar.focus_set()

        tabela = ctk.CTkScrollableFrame(
            frame,
            fg_color=self.cores["surface"],
            border_width=1,
            border_color=self.cores["border"],
            corner_radius=8,
        )
        tabela.pack(fill="both", expand=True)
        tabela.grid_columnconfigure(0, weight=4)
        tabela.grid_columnconfigure(1, weight=5)
        tabela.grid_columnconfigure(2, weight=2)

        for coluna, texto in enumerate(("Arquivo atual", "Novo nome", "Status")):
            ctk.CTkLabel(
                tabela,
                text=texto,
                anchor="w",
                font=("Segoe UI", 10, "bold"),
                text_color=self.cores["fg"],
                fg_color="#f7f7f7",
                corner_radius=0,
            ).grid(row=0, column=coluna, sticky="nsew", padx=(8, 2), pady=(6, 4))

        rotulos = {
            "renomear": "Será renomeado",
            "ignorado": "Já tem o prefixo",
            "conflito_duplicado": "Nome duplicado",
            "conflito_existente": "Destino já existe",
            "nome_muito_longo": "Nome muito longo",
        }
        exibidos = resultados[:MAX_LINHAS_PREVIEW]
        for linha, item in enumerate(exibidos, start=1):
            status = item["status"]
            cor = self.cores["fg"]
            if status == "ignorado":
                cor = self.cores["disabled"]
            elif status != "renomear":
                cor = self.cores["conflict"]
            valores = (item["atual"], item["novo"], rotulos.get(status, status))
            for coluna, texto in enumerate(valores):
                ctk.CTkLabel(
                    tabela,
                    text=texto,
                    anchor="w",
                    justify="left",
                    font=FONTE_PEQUENA,
                    text_color=cor,
                    wraplength=(280 if coluna == 0 else 340 if coluna == 1 else 160),
                ).grid(row=linha, column=coluna, sticky="nsew", padx=(8, 2), pady=4)

        ocultos = len(resultados) - len(exibidos)
        if ocultos > 0:
            ctk.CTkLabel(
                tabela,
                text=f"… e mais {ocultos} arquivo(s). O resumo acima considera todos.",
                anchor="w",
                font=FONTE_PEQUENA,
                text_color=self.cores["muted"],
            ).grid(
                row=len(exibidos) + 1,
                column=0,
                columnspan=3,
                sticky="ew",
                padx=8,
                pady=10,
            )

        self._centralizar_janela(janela)
        janela.deiconify()

    def _callback_progresso(self, atual: int, total: int, nome: str, acao: str) -> None:
        intervalo = max(1, total // 120)
        if atual == 1 or atual == total or atual % intervalo == 0:
            self.progress_bar.set(atual / max(total, 1))
            self.label_status.configure(text=f"{acao}: {atual}/{total} — {nome}")
            self.root.update_idletasks()

    def _executar_renomeacao(
        self,
        pasta: str,
        prefixo: str,
        prefixo_formatado: str,
        resultados: list[dict[str, str]],
    ) -> None:
        self.service = self.service or RenomeadorService(pasta)
        self.progress_bar.set(0)
        self._set_processando(True, "Preparando diário de segurança...")
        try:
            resultado = self.service.executar(
                resultados,
                prefixo,
                self.log_pasta_atual,
                progresso=lambda atual, total, nome: self._callback_progresso(
                    atual, total, nome, "Renomeando"
                ),
            )
        except PersistenciaErro as exc:
            messagebox.showerror(
                "Operação cancelada com segurança",
                "Não foi possível criar o diário de recuperação. Nenhum arquivo foi alterado.\n\n"
                f"{exc}",
            )
            return
        except Exception as exc:
            messagebox.showerror("Erro inesperado", f"A operação foi interrompida:\n{exc}")
            return
        finally:
            self._set_processando(False)

        self.progress_bar.set(1 if resultado.renomeados else 0)
        self.label_status.configure(
            text=f"Concluído: {resultado.renomeados} renomeado(s), {len(resultado.erros)} erro(s)."
        )
        if resultado.renomeados:
            self._registrar_prefixo_usado(prefixo)
        self._atualizar_estado_desfazer()

        resumo = (
            f"Pasta:\n{pasta}\n\n"
            f"Prefixo utilizado:\n{prefixo_formatado}\n\n"
            f"Arquivos renomeados: {resultado.renomeados}\n"
            f"Arquivos ignorados: {resultado.ignorados}\n"
            f"Arquivos não alterados por conflito/limite: {resultado.conflitos}\n"
            f"Arquivos com erro: {len(resultado.erros)}"
        )
        detalhes = ""
        if resultado.erros:
            detalhes += "\n\nPrimeiros erros:\n" + "\n".join(resultado.erros[:8])
        if resultado.avisos:
            detalhes += "\n\nAvisos:\n" + "\n".join(resultado.avisos)

        if resultado.erros or resultado.avisos:
            messagebox.showwarning("Processo concluído com observações", resumo + detalhes)
        else:
            messagebox.showinfo("Concluído", resumo)
        self._contar_arquivos(pasta)

    # ------------------------------------------------------------------
    # Desfazer
    # ------------------------------------------------------------------
    def desfazer_ultima_operacao(self) -> None:
        pasta = self.pasta_selecionada.get()
        if not pasta or not self.log_pasta_atual:
            messagebox.showinfo("Desfazer", "Não há nenhuma ação registrada nesta pasta.")
            return

        ultima = self.log_pasta_atual[-1]
        itens = ultima.get("itens", [])
        quantidade = len(itens) if isinstance(itens, list) else 0
        confirmar = messagebox.askyesno(
            "Desfazer última ação",
            f"Isso tentará restaurar {quantidade} arquivo(s).\n"
            f"Ação realizada em: {ultima.get('data_exibicao', '?')}\n\nContinuar?",
        )
        if not confirmar:
            return

        self.service = self.service or RenomeadorService(pasta)
        self.progress_bar.set(0)
        self._set_processando(True, "Preparando desfazer seguro...")
        try:
            resultado = self.service.desfazer(
                self.log_pasta_atual,
                progresso=lambda atual, total, nome: self._callback_progresso(
                    atual, total, nome, "Restaurando"
                ),
            )
        except PersistenciaErro as exc:
            messagebox.showerror(
                "Desfazer cancelado com segurança",
                "Não foi possível criar o diário de recuperação. Nenhum arquivo foi restaurado.\n\n"
                f"{exc}",
            )
            return
        except Exception as exc:
            messagebox.showerror("Erro inesperado", f"O desfazer foi interrompido:\n{exc}")
            return
        finally:
            self._set_processando(False)

        self.progress_bar.set(1 if resultado.revertidos else 0)
        self.label_status.configure(
            text=(
                f"Desfazer concluído: {resultado.revertidos} restaurado(s), "
                f"{resultado.pendentes} pendente(s)."
            )
        )
        self._atualizar_estado_desfazer()

        resumo = (
            f"Arquivos restaurados: {resultado.revertidos}\n"
            f"Arquivos ainda pendentes: {resultado.pendentes}\n"
            f"Problemas encontrados: {len(resultado.erros)}"
        )
        detalhes = ""
        if resultado.erros:
            detalhes += "\n\nPrimeiros problemas:\n" + "\n".join(resultado.erros[:8])
        if resultado.avisos:
            detalhes += "\n\nAvisos:\n" + "\n".join(resultado.avisos)

        if resultado.erros or resultado.avisos:
            messagebox.showwarning("Desfazer concluído com observações", resumo + detalhes)
        else:
            messagebox.showinfo("Desfazer concluído", resumo)
        self._contar_arquivos(pasta)


def _arquivo_log_inicializacao() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PREFIX"
    base.mkdir(parents=True, exist_ok=True)
    return base / "startup_error.log"


def executar_autoteste_empacotado() -> int:
    arquivo_erro = Path.cwd() / "PREFIX_selftest_error.txt"
    arquivo_sucesso = Path.cwd() / "PREFIX_selftest_ok.txt"
    root: ctk.CTk | None = None
    try:
        arquivo_erro.unlink(missing_ok=True)
        arquivo_sucesso.unlink(missing_ok=True)
        executar_autoteste_core()
        root = ctk.CTk()
        app = RenomeadorApp(root)
        root.update()
        root.update_idletasks()
        app._exibir_janela_principal()
        root.update()
        assert root.state() == "normal", f"Estado inesperado da janela: {root.state()}"
        assert root.winfo_viewable() == 1, "A janela principal não ficou visível."
        assert root.winfo_width() >= 700 and root.winfo_height() >= 600
        assert app.btn_executar is not None
        assert app.progress_bar is not None
        arquivo_sucesso.write_text("AUTOTESTE APROVADO - JANELA VISIVEL\n", encoding="utf-8")
        return 0
    except Exception:
        arquivo_erro.write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def main() -> None:
    if "--self-test" in sys.argv:
        raise SystemExit(executar_autoteste_empacotado())

    _configurar_dpi_awareness()
    root: ctk.CTk | None = None
    try:
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        root = ctk.CTk()
        RenomeadorApp(root)
        root.mainloop()
    except Exception:
        detalhes = traceback.format_exc()
        try:
            log = _arquivo_log_inicializacao()
            log.write_text(detalhes, encoding="utf-8")
        except Exception:
            log = None
        try:
            if root is None:
                root_alerta = tk.Tk()
                root_alerta.withdraw()
            else:
                root_alerta = root
            complemento = f"\n\nDetalhes salvos em:\n{log}" if log else ""
            messagebox.showerror(
                "PREFIX - erro ao iniciar",
                "O aplicativo não conseguiu abrir a interface." + complemento,
                parent=root_alerta,
            )
        except Exception:
            pass
        raise SystemExit(1)


if __name__ == "__main__":
    main()
