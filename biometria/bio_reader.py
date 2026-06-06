# -*- coding: utf-8 -*-
"""
bio_reader.py - Digital Persona U.are.U 4500
Reconhecimento 1:N eficiente:
  - Captura o dedo UMA vez
  - Compara contra todos os templates em memoria via compare_fmds
  - Sem recaptura por aluno — funciona bem com 500+ alunos
"""

import os
import time
import random
import ctypes
import base64
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_lock_leitor = threading.Lock()  # Garante acesso exclusivo ao leitor


def _setup_dll_paths():
    os.environ['PATH'] = (
        BASE_DIR + ';' +
        r'C:\Windows\System32' + ';' +
        os.environ.get('PATH', ''))
    for p in [BASE_DIR, r'C:\Windows\System32']:
        try:
            os.add_dll_directory(p)
        except Exception:
            pass


class BioReader:
    def __init__(self, simulado=True):
        self.simulado = simulado
        self._lib     = None

    # ── Inicializacao ─────────────────────────────────────────────────────────
    def inicializar(self):
        if self.simulado:
            time.sleep(0.3)
            return True
        try:
            _setup_dll_paths()
            dll_path = os.path.join(BASE_DIR, 'uareu4500.dll')
            if not os.path.exists(dll_path):
                raise FileNotFoundError(
                    f"uareu4500.dll nao encontrada em {dll_path}")

            lib = ctypes.CDLL(dll_path, winmode=0)

            # Captura e retorna FMD como base64
            lib.python_read_fingerprint_and_get_base64_string.restype = ctypes.c_char_p

            # Compara dois FMDs em memoria (sem recaptura)
            lib.compare_fmds.argtypes = [
                ctypes.c_uint, ctypes.POINTER(ctypes.c_ubyte),
                ctypes.c_uint, ctypes.POINTER(ctypes.c_ubyte),
                ctypes.POINTER(ctypes.c_uint)]
            lib.compare_fmds.restype = ctypes.c_int

            self._lib = lib
            return True
        except Exception as e:
            print(f"[BioReader] Erro: {e}")
            return False

    # ── Captura raw ───────────────────────────────────────────────────────────
    def _capturar_raw(self):
        """Captura dedo e retorna (fmd_bytes, b64_str). Thread-safe."""
        with _lock_leitor:
            ptr = self._lib.python_read_fingerprint_and_get_base64_string()
            if not ptr:
                raise RuntimeError("Leitor nao respondeu.")
            b64 = ctypes.string_at(ptr).decode('utf-8')
            if not b64:
                raise RuntimeError("Template vazio.")
            return base64.b64decode(b64), b64

    # ── Cadastro — 3 capturas, salva o melhor ────────────────────────────────
    def capturar(self, progresso_cb=None):
        if self.simulado:
            time.sleep(1.5)
            return bytes(random.getrandbits(8) for _ in range(256))
        return self._capturar_3x(progresso_cb)

    def _capturar_3x(self, cb=None):
        def _cb(msg, pct):
            if cb:
                try:
                    cb(msg, pct)
                except Exception:
                    pass
            print(f"[BioReader] {msg}")

        capturas = []
        for i in range(3):
            if i == 0:
                _cb("Apoie o dedo no leitor...", 0.0)
            else:
                _cb(f"Remova e recoloque o mesmo dedo ({i+1}/3)...", i / 3)
                time.sleep(1.5)

            for tentativa in range(3):
                try:
                    _, b64 = self._capturar_raw()
                    capturas.append(b64)
                    _cb(f"Leitura {i+1}/3 OK!", (i + 1) / 3)
                    break
                except Exception as e:
                    if tentativa < 2:
                        _cb("Leitura ruim, tente novamente...", i / 3)
                        time.sleep(0.5)
                    else:
                        raise RuntimeError(f"Falha apos 3 tentativas: {e}")

        # Melhor template = maior FMD (mais minucias)
        melhor = max(capturas, key=lambda b: len(b))
        _cb("Template salvo com sucesso!", 1.0)
        return melhor.encode('utf-8')

    # ── Reconhecimento 1:N eficiente ─────────────────────────────────────────
    def reconhecer(self, templates):
        if self.simulado:
            return self._reconhecer_simulado(templates)
        return self._reconhecer_sdk(templates)

    def _reconhecer_simulado(self, templates):
        time.sleep(3)
        if templates and random.random() > 0.3:
            return {'aluno_id': random.choice(templates)['aluno_id']}
        return None

    def _reconhecer_sdk(self, templates):
        """
        1. Captura o dedo UMA vez
        2. Compara contra todos os templates em memoria via compare_fmds
        3. Retorna o aluno que bateu
        Eficiente para 500+ alunos — sem recaptura por aluno.
        """
        if not templates or not self._lib:
            return None

        # Captura o dedo uma unica vez
        try:
            fmd_atual, _ = self._capturar_raw()
        except Exception as e:
            print(f"[BioReader] Captura: {e}")
            return None

        buf_atual = (ctypes.c_ubyte * len(fmd_atual))(*fmd_atual)

        # Compara contra todos os templates em memoria
        for t in templates:
            try:
                tmpl = t['template']
                if isinstance(tmpl, (bytes, bytearray)):
                    try:
                        fmd_ref = base64.b64decode(tmpl)
                    except Exception:
                        fmd_ref = bytes(tmpl)
                else:
                    fmd_ref = base64.b64decode(tmpl)

                buf_ref = (ctypes.c_ubyte * len(fmd_ref))(*fmd_ref)
                score   = ctypes.c_uint(0)

                ret = self._lib.compare_fmds(
                    ctypes.c_uint(len(fmd_atual)), buf_atual,
                    ctypes.c_uint(len(fmd_ref)),   buf_ref,
                    ctypes.byref(score))

                if ret == 1:
                    print(f"[BioReader] Match aluno_id={t['aluno_id']} score={score.value}")
                    return {'aluno_id': t['aluno_id']}
                # Threshold estendido: score < 21474 (~1% FAR)
                if ret == 0 and score.value < 21474:
                    print(f"[BioReader] Match estendido aluno_id={t['aluno_id']} score={score.value}")
                    return {'aluno_id': t['aluno_id']}

            except Exception as e:
                print(f"[BioReader] Compare aluno {t['aluno_id']}: {e}")
                continue

        return None

    def __del__(self):
        self._lib = None