# -*- coding: utf-8 -*-
"""
Regra do vencimento da v1.7.2 (_novo_vencimento em app/routes/pagamentos.py),
testada isoladamente (sem banco e sem servidor).

  docker compose exec -T web python tests/pagamentos_v172/teste_regra_vencimento.py

Cada caso traz o resultado esperado para as duas regras:
  'atual'    : novo = data_cobranca + 1 mes, so se for maior que o vencimento atual
  'proposta' : novo = max(vencimento_atual, data_cobranca) + 1 mes  (regra adotada na 1.7.2)

Regra 'proposta' confirmada e aplicada em _novo_vencimento; REGRA abaixo ja
esta em 'proposta'.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.routes.pagamentos import _novo_vencimento  # noqa: E402

REGRA = 'proposta'

D = date
# (descricao, vencimento_atual, data_cobranca, esperado_atual, esperado_proposta)
CASOS = [
    ('sem vencimento',                         None,           D(2026, 11, 11), D(2026, 12, 11), D(2026, 12, 11)),
    ('cobranca 1 dia depois do vencimento',    D(2026, 11, 10), D(2026, 11, 11), D(2026, 12, 11), D(2026, 12, 11)),
    ('cobranca no dia do vencimento',          D(2026, 11, 11), D(2026, 11, 11), D(2026, 12, 11), D(2026, 12, 11)),
    ('cobranca 2 dias antes do vencimento',    D(2026, 11, 13), D(2026, 11, 11), D(2026, 12, 11), D(2026, 12, 13)),
    ('2a cobranca (outra chave) mesmo dia',    D(2026, 12, 11), D(2026, 11, 11), D(2026, 12, 11), D(2027, 1, 11)),
    ('vencimento bem a frente',                D(2027, 1, 20), D(2026, 11, 11), D(2027, 1, 20), D(2027, 2, 20)),
    ('cobranca muito atrasada',                D(2026, 9, 1),  D(2026, 11, 11), D(2026, 12, 11), D(2026, 12, 11)),
    ('fim de mes (31/01 -> 28/02)',            D(2027, 1, 31), D(2027, 1, 31), D(2027, 2, 28), D(2027, 2, 28)),
    ('ano bissexto (29/02/2028)',              D(2028, 2, 20), D(2028, 2, 29), D(2028, 3, 29), D(2028, 3, 29)),
    ('virada de ano',                          D(2026, 12, 15), D(2026, 12, 15), D(2027, 1, 15), D(2027, 1, 15)),
]


def main():
    falhas = 0
    print(f'Regra testada: {REGRA}')
    print(f'{"caso":42} {"venc atual":>11} {"cobranca":>11} {"esperado":>11} {"obtido":>11}')
    for desc, venc, cob, esp_atual, esp_prop in CASOS:
        esperado = esp_atual if REGRA == 'atual' else esp_prop
        obtido = _novo_vencimento(venc, cob)
        ok = obtido == esperado and (venc is None or obtido >= venc)
        falhas += 0 if ok else 1
        print(f'{"[OK]  " if ok else "[FALHA]"} {desc:35} {str(venc):>11} {str(cob):>11} '
              f'{str(esperado):>11} {str(obtido):>11}')
    print()
    print('RESULTADO:', 'TUDO OK' if not falhas else f'{falhas} FALHA(S)')
    return 1 if falhas else 0


if __name__ == '__main__':
    sys.exit(main())
