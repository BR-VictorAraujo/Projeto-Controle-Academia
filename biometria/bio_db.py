# -*- coding: utf-8 -*-
"""bio_db.py - Acesso ao banco PostgreSQL usando psycopg3."""

import psycopg
import psycopg.rows
from datetime import datetime, timedelta


class BioDatabase:
    def __init__(self, cfg):
        self.cfg  = cfg
        self.conn = None
        self._conectar()

    def _conectar(self):
        self.conn = psycopg.connect(
            host=self.cfg.get('host', 'localhost'),
            port=int(self.cfg.get('port', 5432)),
            dbname=self.cfg.get('dbname', 'academia_db'),
            user=self.cfg.get('user', 'postgres'),
            password=self.cfg.get('password', ''),
            connect_timeout=5,
            autocommit=True
        )

    def _cursor(self):
        try:
            self.conn.execute("SELECT 1")
        except Exception:
            self._conectar()
        return self.conn.cursor(row_factory=psycopg.rows.dict_row)

    def testar_conexao(self):
        cur = self._cursor()
        cur.execute("SELECT 1")
        cur.close()

    def listar_alunos(self, filtro=""):
        cur = self._cursor()
        sql = """
            SELECT a.id, a.nome, a.plano, a.vencimento, a.ativo,
                   a.documento,
                   (b.template IS NOT NULL OR b.template2 IS NOT NULL) AS tem_biometria
            FROM alunos a
            LEFT JOIN biometrias b ON b.aluno_id = a.id
            WHERE a.ativo = TRUE
        """
        params = []
        if filtro:
            sql += " AND (LOWER(a.nome) LIKE LOWER(%s) OR a.documento LIKE %s)"
            params.append(f'%{filtro}%')
            params.append(f'%{filtro}%')
        sql += " ORDER BY a.nome"
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]

    def buscar_aluno(self, aluno_id):
        cur = self._cursor()
        cur.execute(
            "SELECT id, nome, plano, vencimento, foto, ativo FROM alunos WHERE id = %s",
            (aluno_id,))
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None

    def _garantir_coluna_template2(self):
        """Cria coluna template2 se nao existir."""
        try:
            cur = self._cursor()
            cur.execute("""
                ALTER TABLE biometrias
                ADD COLUMN IF NOT EXISTS template2 BYTEA
            """)
            cur.close()
        except Exception:
            pass

    def buscar_todos_templates(self):
        """Retorna todos os templates para comparacao 1:N (inclui template2)."""
        self._garantir_coluna_template2()
        cur = self._cursor()
        cur.execute("""
            SELECT aluno_id, template, template2
            FROM biometrias
            WHERE template IS NOT NULL OR template2 IS NOT NULL
        """)
        rows = cur.fetchall()
        cur.close()
        templates = []
        for r in rows:
            if r['template']:
                templates.append({
                    'aluno_id': r['aluno_id'],
                    'template': bytes(r['template'])
                })
            if r.get('template2'):
                templates.append({
                    'aluno_id': r['aluno_id'],
                    'template': bytes(r['template2'])
                })
        return templates

    def salvar_templates(self, aluno_id, template1, template2):
        if template1 is None and template2 is None:
            raise ValueError("Nenhuma digital capturada.")

        self._garantir_coluna_template2()
        cur = self._cursor()
        cur.execute(
            "SELECT id FROM biometrias WHERE aluno_id = %s", (aluno_id,))
        existe = cur.fetchone()
        if existe:
            cur.execute("""
                UPDATE biometrias
                SET template  = COALESCE(%s, template),
                    template2 = COALESCE(%s, template2),
                    atualizado_em = NOW()
                WHERE aluno_id = %s
            """, (template1, template2, aluno_id))
        else:
            cur.execute("""
                INSERT INTO biometrias (aluno_id, template, template2, cadastrado_em, atualizado_em)
                VALUES (%s, %s, %s, NOW(), NOW())
            """, (aluno_id, template1, template2))

        if template1 is not None:
            cur.execute(
                "UPDATE alunos SET biometria_status = 'cadastrada' WHERE id = %s",
                (aluno_id,))
        if template2 is not None:
            cur.execute(
                "UPDATE alunos SET biometria_2_status = 'cadastrada' WHERE id = %s",
                (aluno_id,))

        cur.close()

    def reconciliar_status_biometria(self):
        self._garantir_coluna_template2()
        cur = self._cursor()
        cur.execute("""
            UPDATE alunos a
            SET biometria_status = 'cadastrada'
            FROM biometrias b
            WHERE b.aluno_id = a.id
              AND b.template IS NOT NULL
              AND a.biometria_status IS DISTINCT FROM 'cadastrada'
        """)
        corrigidos_d1 = cur.rowcount

        cur.execute("""
            UPDATE alunos a
            SET biometria_2_status = 'cadastrada'
            FROM biometrias b
            WHERE b.aluno_id = a.id
              AND b.template2 IS NOT NULL
              AND a.biometria_2_status IS DISTINCT FROM 'cadastrada'
        """)
        corrigidos_d2 = cur.rowcount
        cur.close()
        return corrigidos_d1 + corrigidos_d2

    def registrar_acesso_biometria(self, aluno_id):
        """
        Fallback: grava acesso direto no banco quando a API Flask não responde.

        Verifica se o aluno está ativo antes de gravar — evita registrar
        acesso de aluno inativo quando o Flask está offline. Retorna um
        dict com o resultado para que o FingerPoint possa exibir o estado
        correto na tela (verde/vermelho/laranja).
        """
        from datetime import date
        cur = self._cursor()

        # Busca aluno com os campos necessários para validação
        cur.execute("""
            SELECT id, nome, plano, vencimento, ativo, foto
            FROM alunos WHERE id = %s
        """, (aluno_id,))
        aluno = cur.fetchone()

        if not aluno:
            cur.close()
            return {'ok': False, 'erro': 'Aluno não encontrado'}

        # Bloqueia aluno inativo
        if not aluno['ativo']:
            cur.close()
            return {
                'ok':      False,
                'bloqueado': True,
                'inativo': True,
                'nome':    aluno['nome'],
                'plano':   aluno['plano'] or '',
                'vencimento': aluno['vencimento'].strftime('%d/%m/%Y') if aluno['vencimento'] else '—',
            }

        hoje    = date.today()
        vencido = bool(aluno['vencimento'] and aluno['vencimento'] < hoje)

        cur.execute("""
            INSERT INTO registros_acesso (aluno_id, tipo, entrada_em)
            VALUES (%s, 'biometria', NOW())
        """, (aluno_id,))
        cur.close()

        return {
            'ok':        True,
            'nome':      aluno['nome'],
            'plano':     aluno['plano'] or '',
            'vencido':   vencido,
            'inativo':   False,
            'vencimento': aluno['vencimento'].strftime('%d/%m/%Y') if aluno['vencimento'] else '—',
        }

    def acessos_hoje_biometria(self):
        """Retorna acessos via biometria de hoje."""
        cur = self._cursor()
        try:
            cur.execute(
                "SELECT valor FROM configuracoes WHERE chave = 'fuso_horario'")
            row = cur.fetchone()
            fuso = int(row['valor']) if row else -3
        except Exception:
            fuso = -3

        hoje_utc_inicio = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0)
        hoje_utc_inicio = hoje_utc_inicio - timedelta(hours=fuso)
        hoje_utc_fim    = hoje_utc_inicio + timedelta(days=1)

        cur.execute("""
            SELECT r.entrada_em, a.nome, a.plano
            FROM registros_acesso r
            JOIN alunos a ON a.id = r.aluno_id
            WHERE r.tipo = 'biometria'
              AND r.entrada_em >= %s
              AND r.entrada_em <  %s
            ORDER BY r.entrada_em DESC
            LIMIT 20
        """, (hoje_utc_inicio, hoje_utc_fim))
        rows = cur.fetchall()
        cur.close()

        result = []
        for r in rows:
            dt_local = r['entrada_em'] + timedelta(hours=fuso)
            result.append({
                'hora' : dt_local.strftime('%H:%M'),
                'nome' : r['nome'],
                'plano': r['plano'] or '',
            })
        return result