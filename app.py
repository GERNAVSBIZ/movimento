# -*- coding: utf-8 -*-

from flask import Flask, render_template, request, jsonify
import re
from datetime import datetime
import io
import os
import traceback

# --- Novas importações do Firebase ---
import firebase_admin
from firebase_admin import credentials, firestore

print("--- [LOG] 1. Aplicação iniciando ---")

# --- Inicialização Robusta do Firebase Admin SDK ---
FIREBASE_INIT_ERROR = None
db = None

try:
    credentials_path = "firebase-credentials.json"
    if os.path.exists(credentials_path):
        print(f"--- [LOG] 2. Arquivo '{credentials_path}' encontrado. ---")
    else:
        print(f"--- [LOG] 2. AVISO CRÍTICO: Arquivo '{credentials_path}' NÃO foi encontrado. Verifique o 'Secret File' no Render. ---")

    cred = credentials.Certificate(credentials_path)
    print("--- [LOG] 3. Objeto de credenciais criado com sucesso. ---")
    
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    
    print("--- [LOG] 4. Firebase App inicializado com sucesso. ---")
    db = firestore.client()
    print("--- [LOG] 5. Cliente Firestore conectado. A inicialização foi um sucesso! ---")

except Exception as e:
    FIREBASE_INIT_ERROR = f"ERRO CRÍTICO: Falha na inicialização do Firebase. Detalhes: {e}"
    print(f"--- [LOG] X. {FIREBASE_INIT_ERROR} ---")

app = Flask(__name__)
print("--- [LOG] 6. Instância do Flask criada. ---")

# --- GERENCIADOR DE ERRO GLOBAL ---
# Esta função garante que qualquer erro não tratado retorne uma resposta JSON,
# evitando o erro de "unexpected character '<'" no frontend.
@app.errorhandler(Exception)
def handle_exception(e):
    # Loga o erro completo no servidor para depuração
    print(f"--- ERRO GLOBAL NÃO TRATADO ---")
    print(traceback.format_exc())
    print(f"--- FIM DO ERRO ---")
    # Retorna uma resposta JSON para o cliente
    response = jsonify({"error": "Ocorreu um erro interno no servidor.", "details": str(e)})
    response.status_code = 500
    return response

def parse_data_file(text_stream):
    records = []
    for line in text_stream:
        if len(line.strip()) <= 50 or line.startswith('SBIZAIZ0'):
            continue
        record = {'timestamp': None, 'matricula': 'N/A', 'tipo_aeronave': 'N/A', 'destino': 'N/A', 'regra_voo': 'N/A', 'pista': '', 'responsavel': 'N/A'}
        try:
            operator_match = re.search(r'\S+$', line.strip())
            if operator_match: record['responsavel'] = operator_match.group(0)
            record['matricula'] = line[15:22].strip()
            record['tipo_aeronave'] = line[22:27].strip()
            rule_match = re.search(r'(IV|VV)', line)
            if rule_match:
                record['regra_voo'] = rule_match.group(0).replace('IV', 'IFR').replace('VV', 'VFR')
                rule_index = rule_match.start()
                string_after_rule = line[rule_index + 2:]
                pista_match = re.search(r'(\d{2})', string_after_rule)
                record['pista'] = pista_match.group(1) if pista_match else ''
                string_before_rule = line[:rule_index]
                time_matches = re.findall(r'\d{4}', string_before_rule)
                if time_matches:
                    horario_str = time_matches[-1]
                    time_index = string_before_rule.rfind(horario_str)
                    record['destino'] = line[27:time_index].strip() or 'N/A'
                    try:
                        data_str = line[9:15]
                        dt_obj = datetime.strptime(f"{data_str}{horario_str}", '%d%m%y%H%M')
                        record['timestamp'] = dt_obj
                    except (ValueError, IndexError): pass
            records.append(record)
        except Exception:
            records.append(record)
    return records

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/get-files', methods=['GET'])
def get_files():
    if FIREBASE_INIT_ERROR: return jsonify({"error": FIREBASE_INIT_ERROR}), 500
    
    files_ref = db.collection(u'files')
    docs = files_ref.stream()
    
    file_list = []
    for doc in docs:
        doc_data = doc.to_dict()
        if not doc_data: continue

        upload_timestamp_iso = None
        upload_timestamp = doc_data.get('uploadTimestamp')
        if isinstance(upload_timestamp, datetime):
            upload_timestamp_iso = upload_timestamp.isoformat() + 'Z'
        
        file_list.append({
            "id": doc.id, 
            "data": {
                "fileName": doc_data.get('fileName', 'Nome desconhecido'),
                "uploadTimestamp": upload_timestamp_iso,
                "recordCount": doc_data.get('recordCount', 0)
            }
        })
    
    file_list.sort(key=lambda x: x['data']['uploadTimestamp'] or '', reverse=True)
    return jsonify(file_list)

@app.route('/api/upload', methods=['POST'])
def upload_file_and_save_to_firestore():
    if FIREBASE_INIT_ERROR: return jsonify({"error": FIREBASE_INIT_ERROR}), 500
    if 'dataFile' not in request.files: return jsonify({"error": "Nenhum arquivo enviado"}), 400
    file = request.files['dataFile']
    if file.filename == '': return jsonify({"error": "Nome de arquivo inválido"}), 400
    
    text_stream = io.TextIOWrapper(file.stream, encoding='utf-8', errors='ignore')
    records = parse_data_file(text_stream)
    if not records: return jsonify({"error": "Nenhum registro válido encontrado"}), 400
    
    file_doc_ref = db.collection(u'files').document()
    file_doc_ref.set({'fileName': file.filename, 'uploadTimestamp': firestore.SERVER_TIMESTAMP, 'recordCount': len(records)})
    
    records_collection = db.collection(u'flight_records')
    batch = db.batch()
    for i, record in enumerate(records):
        record['fileId'] = file_doc_ref.id
        batch.set(records_collection.document(), record)
        if (i + 1) % 499 == 0:
            batch.commit()
            batch = db.batch()
    batch.commit()
    return jsonify({"success": f"{len(records)} registros de '{file.filename}' foram salvos."})

@app.route('/api/flights', methods=['GET'])
def get_flights_from_firestore():
    if FIREBASE_INIT_ERROR: return jsonify({"error": FIREBASE_INIT_ERROR}), 500
    
    file_id = request.args.get('fileId')
    start_date_str = request.args.get('startDate')
    end_date_str = request.args.get('endDate')
    filter_value = request.args.get('filterValue', '').strip().upper()

    if not file_id: return jsonify([]) 

    query = db.collection(u'flight_records').where(u'fileId', u'==', file_id)
    
    # Adicionando tratamento de erro para as datas
    try:
        if start_date_str: query = query.where(u'timestamp', u'>=', datetime.strptime(start_date_str, '%Y-%m-%d'))
        if end_date_str: query = query.where(u'timestamp', u'<=', datetime.strptime(f"{end_date_str} 23:59:59", '%Y-%m-%d %H:%M:%S'))
    except ValueError:
        return jsonify({"error": "Formato de data inválido. Use AAAA-MM-DD."}), 400

    docs = query.stream()
    all_flights = []
    for doc in docs:
        flight_data = doc.to_dict()
        if isinstance(flight_data.get('timestamp'), datetime):
            flight_data['timestamp'] = flight_data['timestamp'].isoformat() + 'Z'
        all_flights.append(flight_data)
    
    if filter_value:
        flights = [f for f in all_flights if filter_value in f.get('matricula', '').upper() or filter_value in f.get('destino', '').upper() or filter_value in f.get('tipo_aeronave', '').upper()]
    else:
        flights = all_flights

    flights.sort(key=lambda x: x.get('timestamp') or '', reverse=True)
    return jsonify(flights)

if __name__ == '__main__':
    app.run(debug=True)

