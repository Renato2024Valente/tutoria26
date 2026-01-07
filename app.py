
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from sqlalchemy import create_engine, Integer, String, DateTime, Text, ForeignKey, inspect, text as sqltext
from sqlalchemy.orm import DeclarativeBase, mapped_column, relationship, sessionmaker, scoped_session
import os, json, re
from dotenv import load_dotenv

load_dotenv()

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = 'users'
    id = mapped_column(Integer, primary_key=True)
    username = mapped_column(String(80), unique=True, nullable=False)
    password_hash = mapped_column(String(255), nullable=False)
    role = mapped_column(String(20), default='professor')
    created_at = mapped_column(DateTime, default=datetime.utcnow)
    tutorias = relationship('Tutoria', back_populates='professor')

class Tutoria(Base):
    __tablename__ = 'tutorias'
    id = mapped_column(Integer, primary_key=True)
    professor_id = mapped_column(Integer, ForeignKey('users.id'), nullable=False)

    # NOVO
    nome_tutor = mapped_column(String(120))

    nome_aluno = mapped_column(String(150), nullable=False)
    serie = mapped_column(String(20), nullable=False)
    tel_aluno = mapped_column(String(30))

    # legado (não exibido na UI)
    tel_resp = mapped_column(String(30))

    contatos_extra = mapped_column(Text)  # JSON: [{nome, telefone}]
    projeto_vida = mapped_column(Text)
    descricoes = mapped_column(Text)
    ocorrencias = mapped_column(Text)     # CSV
    assinatura = mapped_column(Text)      # base64 PNG

    carimbo_resp = mapped_column(String(120))
    carimbo_inst = mapped_column(String(160))
    carimbo_contato = mapped_column(String(160))
    carimbo_texto = mapped_column(String(80))
    carimbo_obs  = mapped_column(Text)    # NOVO: Observações do carimbo

    criado_em = mapped_column(DateTime, default=datetime.utcnow)
    atualizado_em = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    professor = relationship('User', back_populates='tutorias')

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://tutoria2026_0y91_user:O9JNf7QjcPjhUXWFNgfppbWAXx5I52SX@dpg-d5cg4jmuk2gs7380ql2g-a.oregon-postgres.render.com/tutoria2026_0y91')
# IMPORTANT:
# - If DATABASE_URL comes as "postgresql://...", SQLAlchemy defaults to psycopg2.
# - This project uses psycopg3 (psycopg[binary]), so we normalize to the psycopg driver.
if DATABASE_URL.startswith('postgresql://'):
    DATABASE_URL = DATABASE_URL.replace('postgresql://', 'postgresql+psycopg://', 1)
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql+psycopg://', 1)

# Render EXTERNAL DB usually requires sslmode=require when accessed from outside Render
if 'render.com' in DATABASE_URL and 'sslmode=' not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL + ('&' if '?' in DATABASE_URL else '?') + 'sslmode=require'

# Log (sem expor senha)
try:
    safe = re.sub(r"//([^:]+):([^@]+)@", r"//\1:***@", DATABASE_URL)
    print('[DB]', safe)
except Exception:
    pass


SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key')

# PIN para entrar no painel da gestão
GESTAO_PIN = os.getenv('GESTAO_PIN', 'admin1243')

# Senha obrigatória para APAGAR tutorias (individual / selecionadas / todas)
GESTAO_DELETE_PASS = os.getenv('GESTAO_DELETE_PASS', '1243##')

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
Base.metadata.create_all(engine)

def ensure_schema():
    insp = inspect(engine)
    try:
        cols = {c['name'] for c in insp.get_columns('tutorias')}
    except Exception:
        Base.metadata.create_all(engine)
        cols = {c['name'] for c in insp.get_columns('tutorias')}
    needed = {
        'contatos_extra': 'TEXT',
        'assinatura': 'TEXT',
        'carimbo_resp': 'TEXT',
        'carimbo_inst': 'TEXT',
        'carimbo_contato': 'TEXT',
        'carimbo_texto': 'TEXT',
        'carimbo_obs': 'TEXT',   # <- novo campo
        'nome_tutor': 'TEXT',    # <- novo campo
    }
    with engine.begin() as conn:
        for name, typ in needed.items():
            if name not in cols:
                conn.execute(sqltext(f'ALTER TABLE tutorias ADD COLUMN {name} {typ}'))
ensure_schema()

SessionLocal = scoped_session(sessionmaker(bind=engine, expire_on_commit=False))

app = Flask(__name__)
app.secret_key = SECRET_KEY

SERIES = ['6A','6B','6C','6D','7A','7B','7C','7D','8A','8B','8C','8D','9A','9B','9C','9D','1EM-A','1EM-B','1EM-C','2EM-A','2TEC','3EM-A','3EM-B']
OCORRENCIAS = ['Pessoal','Pedagogico','Familia','Prova paulista','Notas Bimestrais','Conflitos/Bullying','Comportamentos','Desatenção','Desrespeito','Emergencial']

def ensure_seed():
    db = SessionLocal()
    if not db.query(User).filter_by(username='gestao').first():
        db.add(User(username='gestao', password_hash=generate_password_hash(os.getenv('APP_ADMIN_PASS','bicudoadmin2526')), role='gestao'))
    if not db.query(User).filter_by(username='renato').first():
        db.add(User(username='renato', password_hash=generate_password_hash(os.getenv('SEED_PROF_PASS','1234')), role='professor'))
    db.commit(); db.close()
ensure_seed()

# ---------- Auth ----------
@app.get('/cadastro')
def cadastro_get():
    if session.get('uid'): return redirect(url_for('form'))
    return render_template('cadastro.html')

@app.post('/cadastro')
def cadastro_post():
    username = request.form.get('username','').strip()
    password = request.form.get('password','').strip()
    if not username or not password:
        return render_template('cadastro.html', error='Informe usuário e senha.')
    db = SessionLocal()
    if db.query(User).filter_by(username=username).first():
        db.close(); return render_template('cadastro.html', error='Usuário já existe.')
    u = User(username=username, password_hash=generate_password_hash(password), role='professor')
    db.add(u); db.commit(); db.close()
    return render_template('login.html', info='Cadastro feito. Entre com suas credenciais.')

@app.get('/login')
def login_get():
    if session.get('uid'): return redirect(url_for('form'))
    return render_template('login.html')

@app.post('/login')
def login_post():
    username = request.form.get('username','').strip()
    password = request.form.get('password','')
    db = SessionLocal()
    u = db.query(User).filter_by(username=username).first()
    ok = u and check_password_hash(u.password_hash, password)
    if ok:
        session['uid'] = u.id; session['role'] = u.role; session['username'] = u.username
        db.close(); return redirect(url_for('form'))
    db.close()
    return render_template('login.html', error='Usuário ou senha inválidos.')

@app.get('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_get'))

# ---------- Views ----------
@app.get('/')
def home():
    if session.get('uid'): return redirect(url_for('form'))
    return redirect(url_for('login_get'))

@app.get('/form')
def form():
    if not session.get('uid'): return redirect(url_for('login_get'))
    db = SessionLocal()
    tid = request.args.get('id')
    duplicar = request.args.get('duplicar') == '1'
    record = None; contatos_json = '[]'
    if tid:
        record = db.get(Tutoria, int(tid))
        if not record: db.close(); abort(404)
        if session.get('role') != 'gestao' and record.professor_id != session['uid']:
            db.close(); abort(403)
        if duplicar:
            class D: pass
            d = D()
            d.id = None
            d.nome_tutor = record.nome_tutor
            d.nome_aluno = record.nome_aluno
            d.serie = record.serie
            d.tel_aluno = record.tel_aluno
            d.contatos_extra = record.contatos_extra
            d.projeto_vida = record.projeto_vida
            d.descricoes = record.descricoes
            d.ocorrencias = record.ocorrencias
            d.assinatura = record.assinatura
            record = d
    if record and record.contatos_extra:
        contatos_json = record.contatos_extra
    db.close()
    return render_template('form.html', SERIES=SERIES, OCORRENCIAS=OCORRENCIAS, record=record, contatos_json=contatos_json)

@app.get('/lista')
def lista():
    if not session.get('uid'): return redirect(url_for('login_get'))
    db = SessionLocal()
    q = db.query(Tutoria)
    if session.get('role') != 'gestao':
        q = q.filter(Tutoria.professor_id == session['uid'])
    tutorias = q.order_by(Tutoria.criado_em.desc()).all()
    db.close()
    return render_template('lista.html', tutorias=tutorias)

# ---------- Gestão com PIN por sessão ----------
@app.get('/gestao')
def gestao_pin():
    if not session.get('uid'): return redirect(url_for('login_get'))
    return render_template('gestao_pin.html')

@app.post('/gestao')
def gestao_pin_post():
    if not session.get('uid'): return redirect(url_for('login_get'))
    pin = request.form.get('pin','').strip()
    if pin == GESTAO_PIN:
        session['gestao_mode'] = True
        return redirect(url_for('gestao_painel'))
    return render_template('gestao_pin.html', error='PIN incorreto.')

@app.get('/gestao/painel')
def gestao_painel():
    if not session.get('gestao_mode'): return redirect(url_for('gestao_pin'))
    return render_template('gestao.html')

@app.post('/gestao/bloquear')
def gestao_bloquear():
    session.pop('gestao_mode', None)
    return redirect(url_for('gestao_pin'))

# ---------- APIs protegidas por sessão de gestão ----------
def require_gestao():
    if not session.get('gestao_mode'):
        abort(403)

def require_delete_pass(data: dict):
    """Senha obrigatória para ações destrutivas no painel da gestão."""
    senha = (data.get('senha') or '').strip()
    if senha != GESTAO_DELETE_PASS:
        return jsonify({'ok': False, 'error': 'Senha de exclusão inválida.'}), 401
    return None

@app.get('/api/gestao/dbinfo')
def api_g_dbinfo():
    require_gestao()
    info = {}
    try:
        info['database_url'] = engine.url.render_as_string(hide_password=True)
        info['dialect'] = engine.url.get_backend_name()
    except Exception:
        info['database_url'] = 'unknown'
        info['dialect'] = 'unknown'
    try:
        insp = inspect(engine)
        info['tables'] = insp.get_table_names()
    except Exception:
        info['tables'] = []
    try:
        db = SessionLocal()
        info['count_tutorias'] = db.query(Tutoria).count()
        db.close()
    except Exception:
        info['count_tutorias'] = None
    return jsonify({'ok': True, **info})

@app.get('/api/gestao/professores')
def api_g_professores():
    require_gestao()
    db = SessionLocal()
    users = db.query(User).order_by(User.username.asc()).all()
    res = [{'id': u.id, 'username': u.username, 'role': u.role} for u in users]
    db.close()
    return jsonify(res)

@app.get('/api/gestao/tutorias')
def api_g_tutorias():
    require_gestao()
    db = SessionLocal()
    items = db.query(Tutoria).order_by(Tutoria.criado_em.desc()).all()
    res = []
    for t in items:
        res.append({
            'id': t.id,
            'professor_id': t.professor_id,
            'nome_tutor': t.nome_tutor,
            'nome_aluno': t.nome_aluno,
            'serie': t.serie,
            'tel_aluno': t.tel_aluno,
            'contatos_extra': json.loads(t.contatos_extra or '[]'),
            'projeto_vida': t.projeto_vida,
            'descricoes': t.descricoes,
            'ocorrencias': (t.ocorrencias or '').split(',') if t.ocorrencias else [],
            'assinatura': t.assinatura or '',
            'carimbo': {
                'resp': t.carimbo_resp,
                'inst': t.carimbo_inst,
                'contato': t.carimbo_contato,
                'texto': t.carimbo_texto,
                'obs': t.carimbo_obs,
            },
            'criado_em': t.criado_em.isoformat(),
        })
    db.close()
    return jsonify(res)

@app.post('/api/gestao/carimbo')
def api_g_carimbo_all():
    require_gestao()
    data = request.json or {}
    resp = (data.get('resp') or '').strip()
    inst = (data.get('inst') or '').strip()
    contato = (data.get('contato') or '').strip()
    texto = (data.get('texto') or 'ÊXITO VISTADO').strip()
    obs   = (data.get('obs')   or '').strip()
    db = SessionLocal()
    n = 0
    for t in db.query(Tutoria).all():
        t.carimbo_resp = resp
        t.carimbo_inst = inst
        t.carimbo_contato = contato
        t.carimbo_texto = texto
        t.carimbo_obs = obs
        n += 1
    db.commit(); db.close()
    return jsonify({'ok': True, 'aplicados': n})

@app.post('/api/gestao/tutorias/<int:tid>/carimbo')
def api_g_carimbo_one(tid):
    require_gestao()
    data = request.json or {}
    db = SessionLocal()
    t = db.get(Tutoria, tid)
    if not t: db.close(); abort(404)
    t.carimbo_resp = (data.get('resp') or '').strip()
    t.carimbo_inst = (data.get('inst') or '').strip()
    t.carimbo_contato = (data.get('contato') or '').strip()
    t.carimbo_texto = (data.get('texto') or 'ÊXITO VISTADO').strip()
    t.carimbo_obs = (data.get('obs') or '').strip()
    db.commit(); db.close()
    return jsonify({'ok': True})



# ---------- Gestão: manutenção (excluir tutorias) ----------
@app.delete('/api/gestao/tutorias/<int:tid>')
def api_g_delete_one(tid):
    require_gestao()
    data = request.get_json(silent=True) or {}
    bad = require_delete_pass(data)
    if bad: return bad
    db = SessionLocal()
    t = db.get(Tutoria, tid)
    if not t:
        db.close(); abort(404)
    db.delete(t)
    db.commit(); db.close()
    return jsonify({'ok': True})

@app.post('/api/gestao/tutorias/excluir')
def api_g_delete_many():
    require_gestao()
    data = request.json or {}
    bad = require_delete_pass(data)
    if bad: return bad
    ids = data.get('ids') or []
    # validação básica
    ids = [int(x) for x in ids if str(x).isdigit()]
    if not ids:
        return jsonify({'ok': False, 'error': 'Lista de ids vazia.'}), 400
    db = SessionLocal()
    q = db.query(Tutoria).filter(Tutoria.id.in_(ids))
    n = q.count()
    q.delete(synchronize_session=False)
    db.commit(); db.close()
    return jsonify({'ok': True, 'apagadas': n})

@app.delete('/api/gestao/tutorias')
def api_g_delete_all():
    require_gestao()
    data = request.json or {}
    bad = require_delete_pass(data)
    if bad: return bad
    confirm = (data.get('confirm') or '').strip()
    if confirm != 'APAGAR_TODAS':
        return jsonify({'ok': False, 'error': 'Confirmação inválida. Envie {"confirm":"APAGAR_TODAS"}.'}), 400
    db = SessionLocal()
    n = db.query(Tutoria).count()
    db.query(Tutoria).delete(synchronize_session=False)
    db.commit(); db.close()
    return jsonify({'ok': True, 'apagadas': n})

# ---------- CRUD (professor) ----------
@app.post('/api/tutorias')
def api_create():
    if not session.get('uid'): abort(401)
    data = request.json or {}
    db = SessionLocal()
    t = Tutoria(
        professor_id=session['uid'],
        nome_tutor=data.get('nome_tutor','').strip(),
        nome_aluno=data.get('nome_aluno','').strip(),
        serie=data.get('serie','').strip(),
        tel_aluno=data.get('tel_aluno','').strip(),
        contatos_extra=json.dumps(data.get('contatos_extra', []), ensure_ascii=False),
        projeto_vida=data.get('projeto_vida','').strip(),
        descricoes=data.get('descricoes','').strip(),
        ocorrencias=','.join(data.get('ocorrencias', [])),
        assinatura=data.get('assinatura','')
    )
    db.add(t); db.commit(); rid = t.id; db.close()
    return jsonify({'ok': True, 'id': rid})

@app.put('/api/tutorias/<int:tid>')
def api_update(tid):
    if not session.get('uid'): abort(401)
    data = request.json or {}
    db = SessionLocal()
    t = db.get(Tutoria, tid)
    if not t: db.close(); abort(404)
    if session.get('role') != 'gestao' and t.professor_id != session['uid']:
        db.close(); abort(403)
    t.nome_tutor = data.get('nome_tutor','').strip()
    t.nome_aluno = data.get('nome_aluno','').strip()
    t.serie = data.get('serie','').strip()
    t.tel_aluno = data.get('tel_aluno','').strip()
    t.contatos_extra = json.dumps(data.get('contatos_extra', []), ensure_ascii=False)
    t.projeto_vida = data.get('projeto_vida','').strip()
    t.descricoes = data.get('descricoes','').strip()
    t.ocorrencias = ','.join(data.get('ocorrencias', []))
    t.assinatura = data.get('assinatura','')
    db.commit(); db.close()
    return jsonify({'ok': True})

@app.delete('/api/tutorias/<int:tid>')
def api_delete(tid):
    if not session.get('uid'): abort(401)
    db = SessionLocal()
    t = db.get(Tutoria, tid)
    if not t: db.close(); abort(404)
    if session.get('role') != 'gestao' and t.professor_id != session['uid']:
        db.close(); abort(403)
    db.delete(t); db.commit(); db.close()
    return jsonify({'ok': True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
