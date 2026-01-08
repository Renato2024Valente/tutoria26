
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
from sqlalchemy import create_engine, Integer, String, DateTime, Text, ForeignKey, inspect, text as sqltext
from sqlalchemy.orm import DeclarativeBase, mapped_column, relationship, sessionmaker, scoped_session
import os, json, re, io, base64
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

# PDF
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib.utils import ImageReader

load_dotenv()

# ---- Timezone (Brasília) ----
BR_TZ = ZoneInfo('America/Sao_Paulo')

def _dt_to_br(dt):
    """Converte datetime (naive->assume UTC) para horário de Brasília."""
    if not dt:
        return None
    if getattr(dt, 'tzinfo', None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(BR_TZ)
    except Exception:
        return dt

def _iso_br(dt):
    d = _dt_to_br(dt)
    return d.isoformat() if d else ''

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

@app.template_filter('dtbr')
def dtbr(dt, fmt='%d/%m/%Y %H:%M'):
    """Formata datetime em horário de Brasília."""
    d = _dt_to_br(dt)
    return d.strftime(fmt) if d else ''
@app.template_filter('fromjson')
def fromjson_filter(val, default=None):
    """Converte string JSON em objeto python para usar nos templates."""
    if default is None:
        default = []
    try:
        return json.loads(val) if val else default
    except Exception:
        return default



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

@app.post('/form')
def form_post():
    """Salva a tutoria (create/update) via formulário HTML."""
    if not session.get('uid'):
        return redirect(url_for('login_get'))

    tid = (request.form.get('id') or '').strip()
    nome_tutor = (request.form.get('nome_tutor') or '').strip()
    nome_aluno = (request.form.get('nome_aluno') or '').strip()
    serie = (request.form.get('serie') or '').strip()
    tel_aluno = (request.form.get('tel_aluno') or '').strip()
    contatos_extra = (request.form.get('contatos_extra') or '[]').strip()
    projeto_vida = (request.form.get('projeto_vida') or '').strip()
    descricoes = (request.form.get('descricoes') or '').strip()
    ocorrencias = request.form.getlist('ocorrencias')
    assinatura = (request.form.get('assinatura') or '').strip()
    save_mode = (request.form.get('save_mode') or '').strip().lower()  # 'update' | 'nova'

    if not nome_aluno or not serie:
        return render_template(
            'form.html',
            SERIES=SERIES,
            OCORRENCIAS=OCORRENCIAS,
            record=None,
            contatos_json=contatos_extra or '[]',
            error='Preencha pelo menos Nome do Aluno e Série/Turma.'
        )

    # valida JSON de contatos
    try:
        json.loads(contatos_extra or '[]')
    except Exception:
        contatos_extra = '[]'

    db = SessionLocal()

    # update
    if tid and tid.isdigit():
        t = db.get(Tutoria, int(tid))
        if not t:
            db.close()
            abort(404)
        if session.get('role') != 'gestao' and t.professor_id != session['uid']:
            db.close()
            abort(403)

        # Se o usuário escolheu "Criar NOVA" a partir desta, não altera a original.
        if save_mode == 'nova':
            # exige nova assinatura
            if not assinatura:
                db.close()
                return render_template(
                    'form.html',
                    SERIES=SERIES,
                    OCORRENCIAS=OCORRENCIAS,
                    record=t,
                    contatos_json=contatos_extra or '[]',
                    error='Para criar uma NOVA tutoria a partir desta, faça uma nova assinatura.'
                )

            t_new = Tutoria(
                professor_id=t.professor_id,
                nome_tutor=nome_tutor,
                nome_aluno=nome_aluno,
                serie=serie,
                tel_aluno=tel_aluno,
                contatos_extra=contatos_extra,
                projeto_vida=projeto_vida,
                descricoes=descricoes,
                ocorrencias=','.join([o for o in ocorrencias if o]),
                assinatura=assinatura,
            )
            db.add(t_new)
            db.commit()
            db.close()
            return redirect(url_for('lista'))

        # Modo normal: atualiza a MESMA tutoria e MANTÉM a assinatura existente
        t.nome_tutor = nome_tutor
        t.nome_aluno = nome_aluno
        t.serie = serie
        t.tel_aluno = tel_aluno
        t.contatos_extra = contatos_extra
        t.projeto_vida = projeto_vida
        t.descricoes = descricoes
        t.ocorrencias = ','.join([o for o in ocorrencias if o])
        # assinatura NÃO é alterada no modo de edição padrão
        db.commit()
        db.close()
        return redirect(url_for('lista'))

    # create
    t = Tutoria(
        professor_id=session['uid'],
        nome_tutor=nome_tutor,
        nome_aluno=nome_aluno,
        serie=serie,
        tel_aluno=tel_aluno,
        contatos_extra=contatos_extra,
        projeto_vida=projeto_vida,
        descricoes=descricoes,
        ocorrencias=','.join([o for o in ocorrencias if o]),
        assinatura=assinatura,
    )
    db.add(t)
    db.commit()
    db.close()
    return redirect(url_for('lista'))

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
    # Compat com o frontend (campos esperados no alert)
    return jsonify({
        'ok': True,
        **info,
        'db_url': info.get('database_url'),
        'tutorias_count': info.get('count_tutorias'),
    })


# ---------- PDF (Impressão) ----------
def _safe_filename(name: str) -> str:
    name = (name or '').strip()
    name = re.sub(r'[^a-zA-Z0-9._-]+', '_', name)
    return name.strip('_') or 'tutoria'

def _draw_wrapped(c: canvas.Canvas, text: str, x: float, y: float, max_w: float, font: str, size: int, leading: int = 14):
    """Desenha texto quebrando linhas. Retorna novo y."""
    c.setFont(font, size)
    if not text:
        return y
    # normaliza quebras
    text = str(text).replace('\r\n', '\n').replace('\r', '\n')
    for para in text.split('\n'):
        words = para.split(' ')
        line = ''
        for w in words:
            test = (line + ' ' + w).strip()
            if stringWidth(test, font, size) <= max_w:
                line = test
            else:
                c.drawString(x, y, line)
                y -= leading
                line = w
        if line:
            c.drawString(x, y, line)
            y -= leading
        # espaço entre parágrafos
        y -= 2
    return y

def _maybe_new_page(c: canvas.Canvas, y: float, min_y: float = 80):
    if y < min_y:
        c.showPage()
        w, h = A4
        return h - 50
    return y

def _parse_data_url(data_url: str):
    if not data_url:
        return None
    if ',' not in data_url:
        return None
    head, b64 = data_url.split(',', 1)
    try:
        raw = base64.b64decode(b64)
        return raw
    except Exception:
        return None

def build_tutoria_pdf(t: Tutoria, professor_nome: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    margin = 50
    y = h - margin

    # Cabeçalho
    c.setFont('Helvetica-Bold', 16)
    c.drawString(margin, y, 'Tutoria 2026')
    y -= 22
    c.setFont('Helvetica', 10)
    dt = (_dt_to_br(t.criado_em).strftime('%d/%m/%Y %H:%M') if t.criado_em else '')
    c.drawString(margin, y, f'ID #{t.id}   Data: {dt}   Professor: {professor_nome}')
    y -= 12
    c.line(margin, y, w - margin, y)
    y -= 18

    def field(label, value):
        nonlocal y
        y = _maybe_new_page(c, y)
        c.setFont('Helvetica-Bold', 10)
        c.drawString(margin, y, label)
        y -= 12
        y = _draw_wrapped(c, value or '', margin, y, w - 2*margin, 'Helvetica', 11, leading=14)
        y -= 6

    # Campos
    field('Tutor', t.nome_tutor or '')
    field('Aluno', t.nome_aluno or '')
    field('Série/Turma', t.serie or '')
    field('Telefone do aluno', t.tel_aluno or '')

    # Contatos extras
    y = _maybe_new_page(c, y)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(margin, y, 'Contatos extras (responsáveis / outros)')
    y -= 14
    try:
        contatos = json.loads(t.contatos_extra or '[]')
        if not isinstance(contatos, list):
            contatos = []
    except Exception:
        contatos = []
    if not contatos:
        c.setFont('Helvetica', 11)
        c.drawString(margin, y, 'Nenhum.')
        y -= 16
    else:
        c.setFont('Helvetica-Bold', 10)
        c.drawString(margin, y, 'Nome')
        c.drawString(margin + 300, y, 'Telefone')
        y -= 10
        c.line(margin, y, w - margin, y)
        y -= 14
        c.setFont('Helvetica', 11)
        for ctt in contatos:
            y = _maybe_new_page(c, y)
            nome = (ctt.get('nome') or '').strip() if isinstance(ctt, dict) else ''
            tel = (ctt.get('telefone') or '').strip() if isinstance(ctt, dict) else ''
            c.drawString(margin, y, nome or '-')
            c.drawString(margin + 300, y, tel or '-')
            y -= 16
        y -= 4

    # Textos
    field('Projeto de Vida', t.projeto_vida or '')
    field('Descrições / Observações', t.descricoes or '')

    # Ocorrências
    y = _maybe_new_page(c, y)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(margin, y, 'Ocorrências')
    y -= 14
    occs = (t.ocorrencias or '').split(',') if t.ocorrencias else []
    occs = [o.strip() for o in occs if o.strip()]
    if not occs:
        c.setFont('Helvetica', 11)
        c.drawString(margin, y, 'Nenhuma marcada.')
        y -= 16
    else:
        y = _draw_wrapped(c, ' • ' + '\n • '.join(occs), margin, y, w - 2*margin, 'Helvetica', 11, leading=14)

    # Assinatura + Carimbo (parte inferior)
    y = _maybe_new_page(c, y)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(margin, y, 'Assinatura')
    y -= 14

    sig_raw = _parse_data_url(t.assinatura or '')
    if sig_raw:
        try:
            img = ImageReader(io.BytesIO(sig_raw))
            iw, ih = img.getSize()
            max_w = 320
            scale = min(1.0, max_w / float(iw))
            sw = iw * scale
            sh = ih * scale
            y = _maybe_new_page(c, y, min_y=margin + sh + 120)
            c.drawImage(img, margin, y - sh, width=sw, height=sh, mask='auto')
            y -= sh + 10
        except Exception:
            c.setFont('Helvetica', 11)
            c.drawString(margin, y, 'Sem assinatura (erro ao carregar imagem).')
            y -= 16
    else:
        c.setFont('Helvetica', 11)
        c.drawString(margin, y, 'Sem assinatura.')
        y -= 16

    # Carimbo
    y = _maybe_new_page(c, y, min_y=margin + 120)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(margin, y, 'Carimbo')
    y -= 12
    car_texto = (t.carimbo_texto or 'ÊXITO VISTADO').strip()
    car_inst = (t.carimbo_inst or '').strip()
    car_cont = (t.carimbo_contato or '').strip()
    car_resp = (t.carimbo_resp or '').strip()
    car_obs = (t.carimbo_obs or '').strip()
    has_carimbo = any([car_texto, car_inst, car_cont, car_resp, car_obs])
    if not has_carimbo:
        c.setFont('Helvetica', 11)
        c.drawString(margin, y, 'Sem carimbo.')
        y -= 16
    else:
        box_h = 90 + (18 if car_obs else 0)
        y = _maybe_new_page(c, y, min_y=margin + box_h + 40)
        x0 = margin
        y0 = y - box_h
        c.roundRect(x0, y0, w - 2*margin, box_h, 10, stroke=1, fill=0)
        c.setFont('Helvetica-Bold', 12)
        c.drawString(x0 + 12, y - 18, car_texto)
        c.setFont('Helvetica', 10)
        if car_inst:
            c.drawString(x0 + 12, y - 34, car_inst)
        if car_cont:
            c.drawString(x0 + 12, y - 48, car_cont)
        if car_resp:
            c.setFont('Helvetica-Bold', 10)
            c.drawRightString(x0 + (w - 2*margin) - 12, y - 34, 'Responsável')
            c.setFont('Helvetica', 10)
            c.drawRightString(x0 + (w - 2*margin) - 12, y - 48, car_resp)
        if car_obs:
            c.setFont('Helvetica', 9)
            y_obs = y0 + 10
            _draw_wrapped(c, car_obs, x0 + 12, y_obs + 10, w - 2*margin - 24, 'Helvetica', 9, leading=11)

        y = y0 - 10

    c.showPage()
    c.save()
    return buf.getvalue()


@app.get('/tutorias/<int:tid>/pdf')
def tutoria_pdf(tid: int):
    """Gera PDF individual de uma tutoria."""
    if not session.get('uid'):
        return redirect(url_for('login_get'))
    db = SessionLocal()
    t = db.get(Tutoria, tid)
    if not t:
        db.close(); abort(404)
    if session.get('role') != 'gestao' and t.professor_id != session['uid']:
        db.close(); abort(403)
    prof = db.get(User, t.professor_id)
    professor_nome = prof.username if prof else str(t.professor_id)
    pdf_bytes = build_tutoria_pdf(t, professor_nome)
    db.close()
    filename = _safe_filename(f"tutoria_{t.id}_{t.nome_aluno}.pdf")
    return send_file(io.BytesIO(pdf_bytes), mimetype='application/pdf', as_attachment=False, download_name=filename)

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
            'criado_em': _iso_br(t.criado_em),
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
