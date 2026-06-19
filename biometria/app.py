# -*- coding: utf-8 -*-
"""
Academia Gestao - App de Biometria
Leitor: Digital Persona U.are.U 4500
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import threading
import time
import os
import sys

# ── Versionamento ────────────────────────────────────────────────────────────
APP_NOME       = "FingerPoint"
APP_VERSAO     = "1.2.0"   # Versionamento semantico: MAJOR.MINOR.PATCH
APP_BUILD      = "2026-06-18"
LEITOR_MODELO  = "Digital Persona U.are.U 4500"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

COR_PRINCIPAL = "#FF6B00"
COR_DARK      = "#f0f2f5"
COR_CARD      = "#ffffff"
COR_SUCCESS   = "#2e7d32"
COR_ERROR     = "#c62828"
COR_TEXT      = "#1a1a2e"
COR_SUBTEXT   = "#6c757d"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from bio_config import BioConfig
from bio_db     import BioDatabase
from bio_reader import BioReader
from bio_logger import log_reconhecimento, log_acesso, log_erro, log_leitor


class TelaLogin(ctk.CTkFrame):
    def __init__(self, master, on_login):
        super().__init__(master, fg_color=COR_DARK)
        self.on_login = on_login
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="\U0001f446",
                     font=ctk.CTkFont(size=48)).pack(pady=(40, 0))
        ctk.CTkLabel(self, text=APP_NOME,
                     font=ctk.CTkFont(size=24, weight="bold"),
                     text_color=COR_PRINCIPAL).pack(pady=(4, 2))
        ctk.CTkLabel(self, text=f"v{APP_VERSAO}  —  {LEITOR_MODELO}",
                     font=ctk.CTkFont(size=11),
                     text_color=COR_SUBTEXT).pack(pady=(0, 30))

        card = ctk.CTkFrame(self, fg_color=COR_CARD, corner_radius=12)
        card.pack(padx=60, pady=10, fill="x")

        ctk.CTkLabel(card, text="Configuracao do Banco de Dados",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=COR_TEXT).pack(pady=(20, 10))

        campos = [
            ("Servidor:", "host",     "localhost"),
            ("Porta:",    "port",     "5432"),
            ("Banco:",    "dbname",   "academia_db"),
            ("Usuario:",  "user",     "postgres"),
            ("Senha:",    "password", ""),
        ]
        self._entries = {}
        cfg = BioConfig.load()
        for label, key, default in campos:
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=3)
            ctk.CTkLabel(row, text=label, width=80, anchor="w",
                         text_color=COR_SUBTEXT,
                         font=ctk.CTkFont(size=12)).pack(side="left")
            val  = cfg.get(key, default)
            show = "*" if key == "password" else ""
            e = ctk.CTkEntry(row, show=show, height=32)
            e.insert(0, val)
            e.pack(side="left", fill="x", expand=True)
            self._entries[key] = e

        self._simulado = ctk.BooleanVar(value=cfg.get('simulado', True))
        ctk.CTkCheckBox(card, text="Modo simulado (sem leitor fisico)",
                        variable=self._simulado,
                        fg_color=COR_PRINCIPAL,
                        hover_color="#cc5500").pack(pady=(10, 20))

        ctk.CTkButton(self, text="Conectar",
                      fg_color=COR_PRINCIPAL,
                      hover_color="#cc5500",
                      height=42,
                      font=ctk.CTkFont(size=14, weight="bold"),
                      command=self._conectar).pack(padx=60, pady=10, fill="x")

        self._status = ctk.CTkLabel(self, text="",
                                     text_color=COR_SUBTEXT,
                                     font=ctk.CTkFont(size=11))
        self._status.pack(pady=5)

    def _conectar(self):
        cfg = {k: e.get() for k, e in self._entries.items()}
        cfg['simulado'] = self._simulado.get()
        BioConfig.save(cfg)
        self._status.configure(text="Conectando...", text_color=COR_SUBTEXT)
        self.update()
        try:
            db = BioDatabase(cfg)
            db.testar_conexao()
            self.on_login(cfg, db)
        except Exception as ex:
            self._status.configure(text=f"Erro: {ex}", text_color=COR_ERROR)


class TelaApp(ctk.CTkFrame):
    def __init__(self, master, cfg, db):
        super().__init__(master, fg_color=COR_DARK)
        self.cfg    = cfg
        self.db     = db
        self.reader = BioReader(simulado=cfg.get('simulado', True))
        self._aba_ativa = None
        # Intencao do usuario sobre o reconhecimento. Persiste entre trocas
        # de aba: se o usuario pausou manualmente, nao queremos retomar
        # automaticamente quando ele voltar do Cadastro para a Portaria.
        # Comeca False = "rodar automaticamente" (comportamento padrao).
        self.reconhecimento_pausado_manualmente = False
        self._build()
        self._abrir_aba("portaria")

    def _build(self):
        sidebar = ctk.CTkFrame(self, fg_color="#1a1a2e", width=210, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        ctk.CTkFrame(sidebar, height=4, fg_color=COR_PRINCIPAL,
                     corner_radius=0).pack(fill="x")

        logo_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        logo_frame.pack(fill="x", padx=16, pady=(16, 4))
        # Icone de impressao digital (em vez do braco)
        ctk.CTkLabel(logo_frame, text="\U0001f446",
                     font=ctk.CTkFont(size=26)).pack(side="left", padx=(0, 8))
        nomes = ctk.CTkFrame(logo_frame, fg_color="transparent")
        nomes.pack(side="left")
        ctk.CTkLabel(nomes, text=APP_NOME,
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COR_PRINCIPAL, anchor="w").pack(anchor="w")
        ctk.CTkLabel(nomes, text=f"v{APP_VERSAO}",
                     font=ctk.CTkFont(size=9),
                     text_color="#555", anchor="w").pack(anchor="w")

        ctk.CTkFrame(sidebar, height=1, fg_color="#2a3a5a").pack(
            fill="x", pady=(12, 5))

        self._btn_portaria = ctk.CTkButton(
            sidebar, text="🚪  Portaria",
            fg_color="transparent", hover_color="#2a3a5a", text_color="#e0e0e0",
            anchor="w", height=40,
            font=ctk.CTkFont(size=13),
            command=lambda: self._abrir_aba("portaria"))
        self._btn_portaria.pack(fill="x", padx=12, pady=3)

        self._btn_cadastro = ctk.CTkButton(
            sidebar, text="✋  Cadastro",
            fg_color="transparent", hover_color="#2a3a5a", text_color="#e0e0e0",
            anchor="w", height=40,
            font=ctk.CTkFont(size=13),
            command=lambda: self._abrir_aba("cadastro"))
        self._btn_cadastro.pack(fill="x", padx=12, pady=3)

        ctk.CTkFrame(sidebar, height=1, fg_color="#333").pack(fill="x", padx=16, pady=10)

        self._lbl_leitor = ctk.CTkLabel(
            sidebar, text="  Leitor desconectado",
            font=ctk.CTkFont(size=10), text_color="#9e9e9e",
            wraplength=160)
        self._lbl_leitor.pack(padx=12, pady=5)

        # Exibe modelo do leitor (em modo simulado, indica entre parenteses)
        if self.cfg.get('simulado'):
            txt_leitor = f"{LEITOR_MODELO} (simulado)"
        else:
            txt_leitor = LEITOR_MODELO
        ctk.CTkLabel(sidebar, text=txt_leitor,
                     font=ctk.CTkFont(size=10),
                     text_color=COR_SUBTEXT,
                     wraplength=180).pack(padx=12, pady=(0, 4))

        ctk.CTkButton(sidebar, text="Sair",
                      fg_color="transparent", hover_color="#fdecea",
                      text_color="#9e9e9e", height=32,
                      font=ctk.CTkFont(size=11),
                      command=self.master.voltar_login).pack(
                          side="bottom", fill="x", padx=12, pady=16)

        self._area = ctk.CTkFrame(self, fg_color="#f0f2f5", corner_radius=0)
        self._area.pack(side="left", fill="both", expand=True)

        threading.Thread(target=self._init_leitor, daemon=True).start()

    def _init_leitor(self):
        ok = self.reader.inicializar()
        if ok:
            self._lbl_leitor.configure(
                text="  Leitor conectado", text_color=COR_SUCCESS)
            log_leitor("Leitor conectado com sucesso")
        else:
            self._lbl_leitor.configure(
                text="  Leitor nao encontrado", text_color=COR_ERROR)
            log_leitor("Leitor NAO encontrado na inicializacao")

    def atualizar_status_leitor(self):
        """
        Reavalia o status real do leitor (reader.dispositivo_presente) e
        atualiza a sidebar. Chamado pela AbaPortaria sempre que uma captura
        falha por desconexao, para a UI nao ficar dizendo 'conectado'
        indefinidamente apos a inicializacao.
        """
        presente = getattr(self.reader, 'dispositivo_presente', False)
        if presente:
            self._lbl_leitor.configure(
                text="  Leitor conectado", text_color=COR_SUCCESS)
        else:
            self._lbl_leitor.configure(
                text="  Leitor desconectado", text_color=COR_ERROR)

    def _abrir_aba(self, nome):
        if self._aba_ativa == nome:
            return
        self._aba_ativa = nome
        self._btn_portaria.configure(
            fg_color=COR_PRINCIPAL if nome == "portaria" else "transparent")
        self._btn_cadastro.configure(
            fg_color=COR_PRINCIPAL if nome == "cadastro" else "transparent")
        for w in self._area.winfo_children():
            w.destroy()
        if nome == "portaria":
            AbaPortaria(self._area, self.cfg, self.db, self.reader, self).pack(
                fill="both", expand=True)
        else:
            AbaCadastro(self._area, self.cfg, self.db, self.reader).pack(
                fill="both", expand=True)


class AbaPortaria(ctk.CTkFrame):
    def __init__(self, master, cfg, db, reader, tela_app=None):
        super().__init__(master, fg_color=COR_DARK)
        self.cfg      = cfg
        self.db       = db
        self.reader   = reader
        self.tela_app = tela_app  # referencia direta a TelaApp (nao ao frame pai)
        self._ativo   = False
        self._build()
        # Auto-inicia o reconhecimento ao abrir a Portaria, exceto se o
        # usuario tiver pausado manualmente em uma sessao anterior. Isso
        # respeita a intencao: trocar para Cadastro e voltar nao deve
        # ignorar uma pausa manual deliberada.
        if self.tela_app and not self.tela_app.reconhecimento_pausado_manualmente:
            self.after(500, lambda: self._toggle(manual=False))

    def _build(self):
        hdr = ctk.CTkFrame(self, fg_color=COR_CARD, corner_radius=0, height=60)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Portaria - Controle de Acesso",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=COR_TEXT).pack(side="left", padx=20, pady=15)

        self._card = ctk.CTkFrame(self, fg_color=COR_CARD, corner_radius=16)
        self._card.pack(expand=True, padx=40, pady=30, fill="both")

        self._icone = ctk.CTkLabel(self._card, text="\U0001f446",
                                    font=ctk.CTkFont(size=64))
        self._icone.pack(pady=(40, 10))

        self._lbl_nome = ctk.CTkLabel(
            self._card, text="Aguardando digital...",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=COR_TEXT)
        self._lbl_nome.pack(pady=5)

        self._lbl_plano = ctk.CTkLabel(
            self._card, text="",
            font=ctk.CTkFont(size=14),
            text_color=COR_SUBTEXT)
        self._lbl_plano.pack(pady=3)

        self._lbl_status = ctk.CTkLabel(
            self._card, text="",
            font=ctk.CTkFont(size=13))
        self._lbl_status.pack(pady=5)

        self._barra = ctk.CTkProgressBar(
            self._card, width=300,
            fg_color="#333", progress_color=COR_PRINCIPAL)
        self._barra.pack(pady=15)
        self._barra.set(0)

        self._lbl_instrucao = ctk.CTkLabel(
            self._card,
            text="Apoie o dedo no leitor para registrar a entrada",
            font=ctk.CTkFont(size=12), text_color=COR_SUBTEXT)
        self._lbl_instrucao.pack(pady=5)

        self._btn = ctk.CTkButton(
            self._card, text="  Iniciar reconhecimento",
            fg_color=COR_PRINCIPAL, hover_color="#cc5500",
            height=44, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._toggle)
        self._btn.pack(pady=(20, 10))

        ctk.CTkLabel(self, text="Ultimos acessos do dia",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COR_SUBTEXT).pack(
                         anchor="w", padx=24, pady=(0, 5))

        self._lista = ctk.CTkScrollableFrame(
            self, fg_color=COR_CARD, height=140, corner_radius=10)
        self._lista.pack(fill="x", padx=20, pady=(0, 16))
        self._carregar_historico()

    def _toggle(self, manual=True):
        """
        Alterna entre rodando/pausado.
        manual=True: clique do usuario; intencao persiste entre trocas de aba.
        manual=False: chamada interna (auto-inicio, suspensao por troca de
        aba). Nao altera a intencao persistente do usuario.
        """
        if self._ativo:
            self._ativo = False
            self._btn.configure(
                text="  Iniciar reconhecimento", fg_color=COR_PRINCIPAL)
            self._lbl_instrucao.configure(text="Reconhecimento pausado")
            self._barra.set(0)
            if manual and self.tela_app:
                self.tela_app.reconhecimento_pausado_manualmente = True
            log_reconhecimento(
                f"Reconhecimento PAUSADO ({'manual' if manual else 'automatico'})")
        else:
            self._ativo = True
            self._btn.configure(
                text="  Pausar reconhecimento", fg_color="#555")
            self._lbl_instrucao.configure(text="Apoie o dedo no leitor...")
            if manual and self.tela_app:
                self.tela_app.reconhecimento_pausado_manualmente = False
            log_reconhecimento(
                f"Reconhecimento INICIADO ({'manual' if manual else 'automatico'})")
            threading.Thread(
                target=self._loop_reconhecimento, daemon=True).start()

    def _loop_reconhecimento(self):
        from bio_reader import _lock_leitor
        import traceback
        templates    = []
        ciclo        = 0
        falhas_consecutivas = 0
        while self._ativo:
            try:
                if ciclo % 10 == 0:
                    templates = self.db.buscar_todos_templates()
                    log_reconhecimento(f"Templates recarregados: {len(templates)} cadastrados")
                ciclo += 1

                try:
                    self._barra.set(0.3)
                    self._lbl_instrucao.configure(text="Apoie o dedo no leitor...")
                except Exception:
                    log_erro("Widget destruido durante loop (tela fechada) - encerrando loop normalmente")
                    return

                if not _lock_leitor.acquire(blocking=False):
                    time.sleep(0.2)
                    continue
                _lock_leitor.release()

                resultado = self.reader.reconhecer(templates)
                falhas_consecutivas = 0  # leitor respondeu, reseta contador de falha

                if not self._ativo:
                    return

                if resultado:
                    aluno = self.db.buscar_aluno(resultado['aluno_id'])
                    log_acesso(f"Reconhecido: {aluno.get('nome', '?')} (ID {resultado['aluno_id']})")
                    try:
                        self._barra.set(1)
                        self.after(0, lambda a=aluno: self._registrar_acesso(a))
                    except Exception:
                        log_erro("Widget destruido ao registrar acesso - encerrando loop normalmente")
                        return
                    time.sleep(0.8)
                    try:
                        self._barra.set(0)
                        self._lbl_instrucao.configure(text="Apoie o dedo no leitor...")
                    except Exception:
                        return
                else:
                    try:
                        self._barra.set(0)
                        self._lbl_instrucao.configure(
                            text="❌ Digital não encontrada — tente novamente",
                            text_color=COR_ERROR)
                        # Reflete na sidebar se o leitor caiu (ex: access
                        # violation por dispositivo desconectado/ausente)
                        if self.tela_app:
                            self.tela_app.atualizar_status_leitor()
                    except Exception:
                        return
                    time.sleep(1.5)
                    try:
                        self._lbl_instrucao.configure(
                            text="Apoie o dedo no leitor...",
                            text_color=COR_SUBTEXT)
                    except Exception:
                        return

            except Exception as e:
                falhas_consecutivas += 1
                erro_detalhado = traceback.format_exc()
                log_erro(f"Excecao no loop de reconhecimento (falha #{falhas_consecutivas}): {e}\n{erro_detalhado}")

                if self._ativo:
                    try:
                        self._lbl_status.configure(
                            text=f"Erro: {e}", text_color=COR_ERROR)
                    except Exception:
                        pass

                # Tenta se recuperar em vez de matar o loop de primeira:
                # leitores USB podem falhar pontualmente sem precisar reiniciar o app
                if falhas_consecutivas >= 5:
                    log_erro(f"5 falhas consecutivas - encerrando loop de reconhecimento automaticamente")
                    self._ativo = False
                    try:
                        self.after(0, lambda: self._btn.configure(
                            text="  Iniciar reconhecimento", fg_color=COR_PRINCIPAL))
                        self.after(0, lambda: self._lbl_instrucao.configure(
                            text="Reconhecimento parado por falhas repetidas. Clique para reiniciar.",
                            text_color=COR_ERROR))
                    except Exception:
                        pass
                    break
                else:
                    time.sleep(1)  # pequena pausa antes de tentar de novo
        log_reconhecimento("Loop de reconhecimento finalizado")

    def _registrar_acesso(self, aluno):
        import requests
        from datetime import date

        hoje = date.today()
        plano_vencido = False
        if aluno.get('vencimento'):
            venc = aluno['vencimento']
            if hasattr(venc, 'date'):
                venc = venc.date()
            plano_vencido = venc < hoje

        try:
            resp = requests.post(
                f"http://localhost:{self.cfg.get('flask_port', 5000)}/acessos/biometria",
                json={"aluno_id": aluno['id']},
                timeout=3)
        except Exception as e:
            log_erro(f"Falha ao notificar Flask para {aluno.get('nome','?')}: {e} - gravando direto no banco")
            self.db.registrar_acesso_biometria(aluno['id'])

        self._lbl_nome.configure(
            text=aluno['nome'],
            text_color=COR_ERROR if plano_vencido else COR_SUCCESS)
        self._lbl_plano.configure(
            text=aluno.get('plano') or 'Sem plano',
            text_color=COR_TEXT)

        if plano_vencido:
            self._icone.configure(text="\u26a0\ufe0f")
            self._lbl_status.configure(
                text="PLANO VENCIDO", text_color=COR_ERROR)
        else:
            self._icone.configure(text="\u2705")
            self._lbl_status.configure(
                text="Acesso liberado", text_color=COR_SUCCESS)

        self._carregar_historico()

    def _carregar_historico(self):
        for w in self._lista.winfo_children():
            w.destroy()
        try:
            acessos = self.db.acessos_hoje_biometria()
            if not acessos:
                ctk.CTkLabel(
                    self._lista,
                    text="Nenhum acesso via biometria hoje",
                    text_color=COR_SUBTEXT,
                    font=ctk.CTkFont(size=11)).pack(pady=10)
                return
            for a in acessos[:10]:
                row = ctk.CTkFrame(
                    self._lista, fg_color="#f0f2f5", corner_radius=6)
                row.pack(fill="x", pady=2, padx=4)
                ctk.CTkLabel(row, text=a['hora'], width=50,
                             text_color=COR_SUBTEXT,
                             font=ctk.CTkFont(size=11)).pack(
                                 side="left", padx=8)
                ctk.CTkLabel(row, text=a['nome'],
                             text_color=COR_TEXT,
                             font=ctk.CTkFont(size=12, weight="bold")).pack(
                                 side="left")
                ctk.CTkLabel(row, text=a.get('plano', ''),
                             text_color=COR_SUBTEXT,
                             font=ctk.CTkFont(size=11)).pack(
                                 side="right", padx=8)
        except Exception:
            pass

    def destroy(self):
        # Sinaliza o loop para parar. Nao bloqueia esperando o lock do
        # leitor: o loop verifica self._ativo antes de cada iteracao e
        # encerra naturalmente. O proximo widget (Cadastro) ja consegue
        # adquirir o lock assim que a captura atual terminar.
        if self._ativo:
            self._ativo = False
            log_reconhecimento("Reconhecimento suspenso (saiu da aba Portaria)")
        super().destroy()


class AbaCadastro(ctk.CTkFrame):
    def __init__(self, master, cfg, db, reader):
        super().__init__(master, fg_color=COR_DARK)
        self.cfg        = cfg
        self.db         = db
        self.reader     = reader
        self._aluno_sel = None
        self._templates = [None, None]
        self._build()

    def _build(self):
        hdr = ctk.CTkFrame(self, fg_color=COR_CARD, corner_radius=0, height=60)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Cadastro de Digitais",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=COR_TEXT).pack(side="left", padx=20, pady=15)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=16)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        col_esq = ctk.CTkFrame(body, fg_color=COR_CARD, corner_radius=12)
        col_esq.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        ctk.CTkLabel(col_esq, text="1. Selecione o aluno",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=COR_PRINCIPAL).pack(
                         padx=16, pady=(16, 8), anchor="w")

        busca_frame = ctk.CTkFrame(col_esq, fg_color="transparent")
        busca_frame.pack(fill="x", padx=16, pady=4)

        self._entry_busca = ctk.CTkEntry(
            busca_frame, placeholder_text="Buscar por nome ou CPF...", height=36)
        self._entry_busca.pack(side="left", fill="x", expand=True)
        self._entry_busca.bind("<KeyRelease>", self._filtrar_alunos)

        self._lista_alunos = ctk.CTkScrollableFrame(
            col_esq, fg_color="transparent", height=300)
        self._lista_alunos.pack(fill="both", expand=True, padx=16, pady=8)

        self._lbl_aluno_sel = ctk.CTkLabel(
            col_esq, text="Nenhum aluno selecionado",
            text_color=COR_SUBTEXT, font=ctk.CTkFont(size=11))
        self._lbl_aluno_sel.pack(pady=8)

        ctk.CTkLabel(self._lista_alunos, text='Digite para buscar...',
                     text_color=COR_SUBTEXT, font=ctk.CTkFont(size=11)).pack(pady=20)

        col_dir = ctk.CTkFrame(body, fg_color=COR_CARD, corner_radius=12)
        col_dir.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        ctk.CTkLabel(col_dir, text="2. Capture as digitais",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=COR_PRINCIPAL).pack(
                         padx=16, pady=(16, 4), anchor="w")

        aviso = ctk.CTkFrame(col_dir, fg_color="#fff8e1", corner_radius=8)
        aviso.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(aviso,
                     text="⚠️  Para um template robusto, o mesmo dedo será lido 3 vezes.\n"
                          "Remova e recoloque o dedo a cada solicitação.",
                     font=ctk.CTkFont(size=10),
                     text_color="#795548",
                     justify="left").pack(padx=10, pady=6, anchor="w")

        d1 = ctk.CTkFrame(col_dir, fg_color="#f0f2f5", corner_radius=8)
        d1.pack(fill="x", padx=16, pady=6)
        ctk.CTkLabel(d1, text="Digital 1 (principal)",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COR_TEXT).pack(side="left", padx=12, pady=10)
        self._lbl_d1 = ctk.CTkLabel(d1, text="Nao capturada",
                                     text_color=COR_SUBTEXT,
                                     font=ctk.CTkFont(size=11))
        self._lbl_d1.pack(side="left")
        ctk.CTkButton(d1, text="Capturar",
                      fg_color=COR_PRINCIPAL, hover_color="#cc5500",
                      width=90, height=30,
                      command=lambda: self._capturar(0)).pack(
                          side="right", padx=12)

        d2 = ctk.CTkFrame(col_dir, fg_color="#f0f2f5", corner_radius=8)
        d2.pack(fill="x", padx=16, pady=6)
        ctk.CTkLabel(d2, text="Digital 2 (backup)",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COR_TEXT).pack(side="left", padx=12, pady=10)
        self._lbl_d2 = ctk.CTkLabel(d2, text="Nao capturada",
                                     text_color=COR_SUBTEXT,
                                     font=ctk.CTkFont(size=11))
        self._lbl_d2.pack(side="left")
        ctk.CTkButton(d2, text="Capturar",
                      fg_color=COR_PRINCIPAL, hover_color="#cc5500",
                      width=90, height=30,
                      command=lambda: self._capturar(1)).pack(
                          side="right", padx=12)

        self._lbl_instrucao = ctk.CTkLabel(
            col_dir,
            text="Selecione um aluno e clique em Capturar.\nApoie o dedo no leitor quando solicitado.",
            font=ctk.CTkFont(size=11), text_color=COR_SUBTEXT,
            justify="center")
        self._lbl_instrucao.pack(pady=16)

        self._progress = ctk.CTkProgressBar(
            col_dir, width=280,
            fg_color="#333", progress_color=COR_PRINCIPAL)
        self._progress.set(0)

        self._btn_salvar = ctk.CTkButton(
            col_dir, text="  Salvar digitais",
            fg_color=COR_SUCCESS, hover_color="#1b5e20",
            height=44, font=ctk.CTkFont(size=14, weight="bold"),
            state="disabled",
            command=self._salvar)
        self._btn_salvar.pack(padx=16, pady=16, fill="x")

        self._lbl_resultado = ctk.CTkLabel(
            col_dir, text="", font=ctk.CTkFont(size=12))
        self._lbl_resultado.pack()

    def _carregar_alunos(self, filtro=""):
        for w in self._lista_alunos.winfo_children():
            w.destroy()
        try:
            alunos = self.db.listar_alunos(filtro)
            if not alunos:
                ctk.CTkLabel(self._lista_alunos,
                             text="Nenhum aluno encontrado",
                             text_color=COR_SUBTEXT,
                             font=ctk.CTkFont(size=11)).pack(pady=10)
                return
            for a in alunos:
                tem_bio     = a.get('tem_biometria', False)
                selecionado = self._aluno_sel and self._aluno_sel['id'] == a['id']

                row = ctk.CTkFrame(
                    self._lista_alunos,
                    fg_color="#fff3e8" if selecionado else "#f8f9fa",
                    corner_radius=6)
                row._aluno_id = a['id']
                row.pack(fill="x", pady=2, padx=2)

                var = ctk.BooleanVar(value=selecionado)
                chk = ctk.CTkCheckBox(row, text="", variable=var, width=24,
                                       fg_color=COR_PRINCIPAL,
                                       hover_color="#cc5500",
                                       checkmark_color="white",
                                       command=lambda x=a: self._selecionar_aluno(x))
                chk.pack(side="left", padx=(8, 0))

                bio_text  = "BIO" if tem_bio else "   "
                bio_color = COR_SUCCESS if tem_bio else "#333"
                bio_bg    = "#1b3a1b" if tem_bio else "#2a2a2a"
                ctk.CTkLabel(row, text=bio_text, width=30,
                             text_color=bio_color,
                             fg_color=bio_bg,
                             corner_radius=4,
                             font=ctk.CTkFont(size=9, weight="bold")).pack(
                                 side="left", padx=4, pady=6)

                info = ctk.CTkFrame(row, fg_color="transparent")
                info.pack(side="left", fill="x", expand=True, padx=4)

                ctk.CTkLabel(info,
                             text=a['nome'],
                             text_color=COR_PRINCIPAL if selecionado else COR_TEXT,
                             font=ctk.CTkFont(size=12, weight="bold"),
                             anchor="w").pack(fill="x")

                doc = a.get('documento') or ''
                cpf_plano = f"CPF: {doc if doc else 'Nao cadastrado'}"
                ativo = a.get('ativo', True)
                status_text  = "Ativo" if ativo else "Inativo"
                status_color = "#2e7d32" if ativo else "#c62828"
                status_bg    = "#e8f5e9" if ativo else "#fdecea"

                linha_info = ctk.CTkFrame(info, fg_color="transparent")
                linha_info.pack(fill="x")
                ctk.CTkLabel(linha_info,
                             text=cpf_plano,
                             text_color=COR_SUBTEXT,
                             font=ctk.CTkFont(size=10),
                             anchor="w").pack(side="left")
                ctk.CTkLabel(linha_info,
                             text=status_text,
                             text_color=status_color,
                             fg_color=status_bg,
                             corner_radius=4,
                             font=ctk.CTkFont(size=9, weight="bold"),
                             width=44).pack(side="left", padx=(6, 0))

                row.bind("<Button-1>", lambda e, x=a: self._selecionar_aluno(x))
                for child in row.winfo_children():
                    child.bind("<Button-1>", lambda e, x=a: self._selecionar_aluno(x))
                for child in info.winfo_children():
                    child.bind("<Button-1>", lambda e, x=a: self._selecionar_aluno(x))

        except Exception as e:
            ctk.CTkLabel(self._lista_alunos,
                         text=f"Erro: {e}",
                         text_color=COR_ERROR).pack()

    def _filtrar_alunos(self, event=None):
        termo = self._entry_busca.get()
        if len(termo) >= 2:
            self._carregar_alunos(termo)
        elif len(termo) == 0:
            for w in self._lista_alunos.winfo_children():
                w.destroy()
            ctk.CTkLabel(self._lista_alunos, text='Digite para buscar...',
                         text_color=COR_SUBTEXT,
                         font=ctk.CTkFont(size=11)).pack(pady=20)

    def _selecionar_aluno(self, aluno):
        self._aluno_sel = aluno
        self._lbl_aluno_sel.configure(
            text=f"Selecionado: {aluno['nome']}",
            text_color=COR_SUCCESS)
        self._templates = [None, None]
        self._lbl_d1.configure(text="Nao capturada", text_color=COR_SUBTEXT)
        self._lbl_d2.configure(text="Nao capturada", text_color=COR_SUBTEXT)
        self._btn_salvar.configure(state="disabled")
        self._lbl_resultado.configure(text="")

    def _capturar(self, idx):
        if not self._aluno_sel:
            messagebox.showwarning("Atencao", "Selecione um aluno primeiro.")
            return
        lbl = self._lbl_d1 if idx == 0 else self._lbl_d2
        lbl.configure(text="Iniciando...", text_color=COR_PRINCIPAL)
        self._progress.pack(pady=5)
        self._progress.set(0)

        def progresso_cb(msg, pct):
            try:
                lbl.configure(text=msg, text_color=COR_PRINCIPAL)
                self._progress.set(pct)
            except Exception:
                pass

        def capturar_thread():
            try:
                template = self.reader.capturar(progresso_cb=progresso_cb)
                self._templates[idx] = template
                self._progress.set(1)
                lbl.configure(text="✅ Capturada!", text_color=COR_SUCCESS)
                if any(t is not None for t in self._templates):
                    self._btn_salvar.configure(state="normal")
            except Exception as e:
                lbl.configure(text=f"Erro: {e}", text_color=COR_ERROR)
            finally:
                time.sleep(0.5)
                self._progress.pack_forget()

        threading.Thread(target=capturar_thread, daemon=True).start()

    def _salvar(self):
        if not self._aluno_sel:
            return
        try:
            aluno_id = self._aluno_sel['id']
            tem_bio  = self._aluno_sel.get('tem_biometria', False)
            if tem_bio:
                from tkinter import messagebox as mb
                resp = mb.askyesno(
                    "Atenção",
                    f"{self._aluno_sel['nome']} já tem digitais cadastradas.\n"
                    "Deseja substituir pelas novas capturas?")
                if not resp:
                    return

            self.db.salvar_templates(
                aluno_id,
                self._templates[0],
                self._templates[1])
            from bio_logger import log_cadastro
            log_cadastro(f"Digitais salvas para aluno ID {aluno_id} ({self._aluno_sel.get('nome','?')})")
            self._lbl_resultado.configure(
                text="Digitais salvas com sucesso!",
                text_color=COR_SUCCESS)
            self._carregar_alunos(self._entry_busca.get())
            self._templates = [None, None]
            self._lbl_d1.configure(text="Nao capturada", text_color=COR_SUBTEXT)
            self._lbl_d2.configure(text="Nao capturada", text_color=COR_SUBTEXT)
            self._btn_salvar.configure(state="disabled")
        except Exception as e:
            self._lbl_resultado.configure(
                text=f"Erro ao salvar: {e}", text_color=COR_ERROR)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NOME}  v{APP_VERSAO}  —  {LEITOR_MODELO}")
        self.geometry("900x620")
        self.minsize(800, 560)
        self.configure(fg_color=COR_DARK)
        self._tela_atual = None
        self.protocol("WM_DELETE_WINDOW", self._fechar)
        self.mostrar_login()

    def _fechar(self):
        """Fecha o app completamente ao clicar no X — encerra todas as threads."""
        try:
            if self._tela_atual:
                self._tela_atual.destroy()
        except Exception:
            pass
        self.destroy()
        os._exit(0)

    def mostrar_login(self):
        if self._tela_atual:
            self._tela_atual.destroy()
        self._tela_atual = TelaLogin(self, on_login=self._apos_login)
        self._tela_atual.pack(fill="both", expand=True)

    def _apos_login(self, cfg, db):
        if self._tela_atual:
            self._tela_atual.destroy()
        self._tela_atual = TelaApp(self, cfg, db)
        self._tela_atual.pack(fill="both", expand=True)

    def voltar_login(self):
        self.mostrar_login()


if __name__ == "__main__":
    from bio_logger import log
    log('SISTEMA', f'{APP_NOME} v{APP_VERSAO} (build {APP_BUILD}) iniciado')
    app = App()
    app.mainloop()