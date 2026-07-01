# -*- coding: utf-8 -*-
"""
Versionamento centralizado do sistema web.
Unico ponto de mudanca: alterar aqui propaga para janela, templates,
logs e endpoints de saude.
"""

APP_NOME   = "GymFlow"
APP_VERSAO = "1.5.0"   # Versionamento semantico: MAJOR.MINOR.PATCH
APP_BUILD  = "2026-06-19"


def info():
    """Dict com info de versao — util para context processor e /health."""
    return {
        'app_nome':   APP_NOME,
        'app_versao': APP_VERSAO,
        'app_build':  APP_BUILD,
    }