"""Small database boundary: SQLite locally, PostgreSQL for hosted deployments."""
import re
import sqlite3

class Record(dict):
    def __getitem__(self,key):
        return list(self.values())[key] if isinstance(key,int) else super().__getitem__(key)

class Result:
    def __init__(self,cursor,lastrowid=None):
        self.cursor=cursor
        self.lastrowid=lastrowid
    def fetchone(self):
        r=self.cursor.fetchone()
        return Record(r) if r is not None else None
    def __iter__(self):
        for r in self.cursor: yield Record(r)

class Postgres:
    def __init__(self,url):
        import psycopg
        from psycopg.rows import dict_row
        self.connection=psycopg.connect(url,row_factory=dict_row,connect_timeout=15)
    def execute(self,sql,params=()):
        import psycopg
        if sql=='BEGIN IMMEDIATE':
            return Result(self.connection.execute('SELECT pg_advisory_xact_lock(762401)'))
        if sql.startswith('PRAGMA table_info('):
            table=sql.split('(')[1].rstrip(')')
            return Result(self.connection.execute('SELECT column_name AS name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s',(table,)))
        sql=sql.replace('INTEGER PRIMARY KEY','SERIAL PRIMARY KEY').replace(' COLLATE NOCASE','').replace('?','%s')
        insert=re.match(r'INSERT INTO (\w+)',sql,re.I)
        returning=bool(insert and insert.group(1) not in ('sessions','resets'))
        if returning: sql+=' RETURNING id'
        try:
            cur=self.connection.execute(sql,params)
        except psycopg.IntegrityError as exc:
            raise sqlite3.IntegrityError('A unique or reference constraint was violated.') from exc
        return Result(cur,cur.fetchone()['id'] if returning else None)
    def executescript(self,script):
        for statement in script.split(';'):
            if statement.strip(): self.execute(statement.strip())
    def commit(self): self.connection.commit()
    def rollback(self): self.connection.rollback()
    def close(self): self.connection.close()
