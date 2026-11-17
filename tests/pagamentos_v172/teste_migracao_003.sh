#!/bin/sh
# Cenario g da v1.7.2: migracao 003 num banco descartavel copiado do dev.
# Rodar na raiz do projeto (Git Bash), com o docker compose no ar:
#   sh tests/pagamentos_v172/teste_migracao_003.sh
# Remover depois: docker compose exec -T db dropdb -U gymflow gymflow_mig003_teste
set -u
B=gymflow_mig003_teste
PSQL="docker compose exec -T db psql -U gymflow -d $B -v ON_ERROR_STOP=1 -q"
UP=migrations/003_v1.7.2_pagamentos_unicidade_up.sql
DOWN=migrations/003_v1.7.2_pagamentos_unicidade_down.sql

estado() {
  $PSQL -At -c "SELECT 'indices=' || coalesce(string_agg(indexname, ',' ORDER BY indexname), '-') FROM pg_indexes WHERE indexname IN ('uq_pagamentos_payment_id_gateway','ix_log_integracao_pagamento_criado_em')" \
        -c "SELECT 'schema_migracao_003=' || count(*) FROM schema_migracao WHERE versao='003_v1.7.2_pagamentos_unicidade'" \
        -c "SELECT 'pagamentos=' || count(*) || ' soma_ids=' || coalesce(sum(id),0) || ' com_chave=' || count(payment_id_gateway) FROM pagamentos"
}

echo "== copia do banco de dev para $B =="
docker compose exec -T db sh -c "createdb -U gymflow $B && pg_dump -U gymflow gymflow | psql -q -U gymflow -d $B" >/dev/null || exit 1
estado

echo; echo "== 1) up sem duplicatas -> cria os indices =="
$PSQL < $UP; echo "codigo de saida: $?"
estado
$PSQL -At -c "SELECT indexdef FROM pg_indexes WHERE indexname='uq_pagamentos_payment_id_gateway'"

echo; echo "== 2) up de novo -> idempotente =="
$PSQL < $UP 2>/dev/null; echo "codigo de saida: $?"
estado

echo; echo "== 3) down =="
$PSQL < $DOWN; echo "codigo de saida: $?"
estado

echo; echo "== 4) com duplicata pela chave -> aborta sem alterar nada =="
$PSQL -c "INSERT INTO pagamentos (aluno_id, valor, forma_pagamento, status, data_vencimento, data_pagamento, criado_em, origem, payment_id_gateway)
          SELECT id, 60, 'cartao_recorrente', 'pago', '2026-11-11', '2026-11-11', now(), 'recorrente_gateway', k
            FROM (SELECT min(id) id FROM alunos) a, (VALUES ('a02c1169a9e34c62882c37f2e3ba8c7f'), ('a02c1169a9e34c62882c37f2e3ba8c7f'), ('41f28923bd3040fe9cb1'), ('41f28923bd3040fe9cb1'), ('41f28923bd3040fe9cb1')) v(k)"
echo "antes:"; estado
$PSQL < $UP; echo "codigo de saida: $? (esperado != 0)"
echo "depois:"; estado

echo; echo "== 5) nome que o installer/atualizar_banco.py usa para pular a 003 =="
python -c "import re; print(re.sub(r'(_up)?\.sql$', '', '003_v1.7.2_pagamentos_unicidade_up.sql'))"
