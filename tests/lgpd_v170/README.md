# Testes de aceitação da v1.7.0 (LGPD)

`teste_cenarios.py` cobre os 11 cenários obrigatórios. Ele roda **somente** num banco descartável
cujo nome termina em `_teste`: o próprio script se recusa a rodar em outro banco. Não rode em QAS
nem em produção.

```bash
# 1. Copia o banco de desenvolvimento (já com a migração 001 aplicada)
docker compose exec -T db sh -c 'dropdb -U gymflow --if-exists gymflow_v170_teste && createdb -U gymflow gymflow_v170_teste && pg_dump -U gymflow gymflow | psql -q -U gymflow -d gymflow_v170_teste'

# 2. Executa
docker compose exec -T -e DATABASE_URL=postgresql+psycopg://gymflow:gymflow@db:5432/gymflow_v170_teste web python tests/lgpd_v170/teste_cenarios.py

# 3. Remove o banco descartável
docker compose exec -T db dropdb -U gymflow gymflow_v170_teste
```

`teste_usuarios_admin.py` testa a restrição de `/usuarios` ao admin. Ele usa o mesmo preparo e é
executado da mesma forma, trocando o nome do script.

As últimas execuções estão em `resultado_execucao.txt` e `resultado_usuarios_admin.txt`.
