# Testes de aceitação da v1.7.0 (LGPD)

- `teste_cenarios.py`: os 11 cenários obrigatórios, com pop-up, aceite, recusa, nova versão,
  permissões e carga.
- `teste_usuarios_admin.py`: restrição de `/usuarios` ao admin.

Os dois rodam **somente** num banco descartável cujo nome termina em `_teste`; os próprios scripts se
recusam a rodar em outro banco. Use uma cópia **sem versão de termo publicada** do banco de dev (com a
migração 001 aplicada) e um banco novo para cada script. Não rode em QAS nem em produção.

```bash
# 1. Copia o banco de desenvolvimento
docker compose exec -T db sh -c 'createdb -U gymflow gymflow_v170_teste && pg_dump -U gymflow gymflow | psql -q -U gymflow -d gymflow_v170_teste'

# 2. Executa (com o container web no ar)
docker compose exec -T -e DATABASE_URL=postgresql+psycopg://gymflow:gymflow@db:5432/gymflow_v170_teste web python tests/lgpd_v170/teste_cenarios.py

# 3. Remove o banco descartável
docker compose exec -T db dropdb -U gymflow gymflow_v170_teste
```

As últimas execuções estão em `resultado_execucao.txt` e `resultado_usuarios_admin.txt`.
