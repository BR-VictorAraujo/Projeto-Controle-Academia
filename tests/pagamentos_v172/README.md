# Testes da v1.7.2 (integração de pagamentos)

Rodam só em bancos descartáveis cujo nome termina em `_teste` (os scripts recusam outro banco).
Nada chama a Cielo real: a API é simulada com `unittest.mock`.

| Script | O que testa | Resultado |
|---|---|---|
| `teste_webhooks.py conexao_morta` | conexões do pool mortas (`pg_terminate_backend`), avisos seguidos e simultâneos | `resultado_conexao_morta_ANTES.txt` (código 1.7.1: 500) / `_DEPOIS.txt` |
| `teste_webhooks.py apos_restart` | 1º aviso depois de `docker compose restart db` | `resultado_restart_db.txt` |
| `teste_webhooks.py cenarios` | a, b, b2 (índice único), c, d, e, regra do vencimento, frases do log | `resultado_cenarios.txt` |
| `teste_reconciliacao.py` | reconciliação com a Cielo e motivo da recusa (API simulada) | `resultado_reconciliacao.txt` |
| `teste_asaas.py` | webhook Asaas (duplicidade, data, recusa) | `resultado_asaas.txt` |
| `teste_telas.py` | tela de logs (busca, máscara), recorrências, financeiro/pagamento manual | `resultado_telas.txt` |
| `teste_migracao_003.sh` | migração 003: up, reaplicação, down, abort com duplicatas | `resultado_migracao_003.txt` |
| `teste_ajustes.py` | ajustes finais: reativação indevida, recusa em assinatura encerrada, `data_pagamento`, chave fora do padrão, payload que não é objeto JSON, `hide_parameters` | `resultado_ajustes.txt` |
| `teste_regra_vencimento.py` | regra do vencimento isolada (`_novo_vencimento`), casos com o esperado da regra atual e da proposta | `resultado_regra_vencimento.txt` |

```bash
# banco descartável copiado do dev, com a 003
docker compose exec -T db sh -c 'createdb -U gymflow gymflow_v172_teste && pg_dump -U gymflow gymflow | psql -q -U gymflow -d gymflow_v172_teste'
docker compose exec -T db psql -U gymflow -d gymflow_v172_teste -v ON_ERROR_STOP=1 -f - < migrations/003_v1.7.2_pagamentos_unicidade_up.sql

# servidor de teste na porta 5001 (waitress, como em produção; pip install waitress==3.0.2 se a imagem não tiver)
docker compose exec -d -e DATABASE_URL=postgresql+psycopg://gymflow:gymflow@db:5432/gymflow_v172_teste web \
    sh -c 'python tests/pagamentos_v172/servidor_teste.py > /tmp/srv5001.log 2>&1'

docker compose exec -T web python tests/pagamentos_v172/teste_webhooks.py cenarios
E="-e DATABASE_URL=postgresql+psycopg://gymflow:gymflow@db:5432/gymflow_v172_teste"
docker compose exec -T $E web python tests/pagamentos_v172/teste_reconciliacao.py
docker compose exec -T $E web python tests/pagamentos_v172/teste_asaas.py
docker compose exec -T $E web python tests/pagamentos_v172/teste_telas.py
docker compose exec -T $E web python tests/pagamentos_v172/teste_ajustes.py
docker compose exec -T web python tests/pagamentos_v172/teste_regra_vencimento.py
sh tests/pagamentos_v172/teste_migracao_003.sh

# limpeza
docker compose exec -T web python tests/pagamentos_v172/parar_servidor.py
docker compose exec -T db dropdb -U gymflow gymflow_v172_teste
```
