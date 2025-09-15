# -*- coding: utf-8 -*-

from flask import Flask, render_template, request, jsonify
import re
from datetime import datetime
import io
import os # Importado para verificar a existência do arquivo

# --- Novas importações do Firebase ---
import firebase_admin
from firebase_admin import credentials, firestore

# --- LOG INICIAL ---
print("--- [LOG] 1. Aplicação iniciando ---")

# --- Inicialização Robusta do Firebase Admin SDK ---
FIREBASE_INIT_ERROR = None
db = None

try:
    # --- LOG DE VERIFICAÇÃO DO ARQUIVO ---
    credentials_path = "firebase-credentials.json"
    if os.path.exists(credentials_path):
        print(f"--- [LOG] 2. Arquivo '{credentials_path}' encontrado. ---")
    else:
        print(f"--- [LOG] 2. AVISO: Arquivo '{credentials_path}' NÃO foi encontrado. ---")

    cred = credentials.Certificate(credentials_path)
    print("--- [LOG] 3. Objeto de credenciais criado com sucesso. ---")
    
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    
    print("--- [LOG] 4. Firebase App inicializado com sucesso. ---")
    db = firestore.client()
    print("--- [LOG] 5. Cliente Firestore conectado. A inicialização foi um sucesso! ---")

except Exception as e:
    FIREBASE_INIT_ERROR = f"ERRO CRÍTICO: Falha na inicialização do Firebase. Verifique o 'Secret File' no Render. Detalhes: {e}"
    print(f"--- [LOG] X. {FIREBASE_INIT_ERROR} ---")

app = Flask(__name__)
print("--- [LOG] 6. Instância do Flask criada. O servidor está pronto para receber requisições. ---")


# OTIMIZAÇÃO: A função agora aceita um stream de texto para ler linha por linha
def parse_data_file(text_stream):
    records = []
    
    # Processa o arquivo linha por linha para economizar memória
    for line in text_stream:
        if len(line.strip()) <= 50 or line.startswith('SBIZAIZ0'):
            continue

        record = {
            'timestamp': None, 'matricula': 'N/A', 'tipo_aeronave': 'N/A',
            'destino': 'N/A', 'regra_voo': 'N/A', 'pista': '', 'responsavel': 'N/A'
        }

        try:
            operator_match = re.search(r'\S+$', line.strip())
            if operator_match:
                record['responsavel'] = operator_match.group(0)

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
                        full_datetime_str = f"{data_str}{horario_str}"
                        dt_obj = datetime.strptime(full_datetime_str, '%d%m%y%H%M')
                        record['timestamp'] = dt_obj
                    except (ValueError, IndexError):
                        pass
            
            records.append(record)

        except Exception:
            records.append(record)
    
    return records

@app.route('/')
def index():
    return render_template('index.html')

# Endpoint para buscar a lista de arquivos
@app.route('/api/get-files', methods=['GET'])
def get_files():
    # VERIFICAÇÃO: Checa se houve erro na inicialização antes de prosseguir
    if FIREBASE_INIT_ERROR:
        return jsonify({"error": FIREBASE_INIT_ERROR}), 500
    try:
        files_ref = db.collection(u'files').order_by(u'uploadTimestamp', direction=firestore.Query.DESCENDING)
        docs = files_ref.stream()
        file_list = []
        for doc in docs:
            try: # CORREÇÃO: Adicionado try/except para proteger contra dados malformados
                doc_data = doc.to_dict()
                if 'uploadTimestamp' in doc_data and isinstance(doc_data.get('uploadTimestamp'), datetime):
                     doc_data['uploadTimestamp'] = doc_data['uploadTimestamp'].isoformat() + 'Z'
                file_list.append({"id": doc.id, "data": doc_data})
            except Exception as doc_error:
                print(f"AVISO: Ignorando documento com erro na lista de arquivos. ID: {doc.id}, Erro: {doc_error}")

        return jsonify(file_list)
    except Exception as e:
        return jsonify({"error": f"Erro ao buscar lista de arquivos: {str(e)}"}), 500

# OTIMIZAÇÃO: Salva os registros em lotes para evitar sobrecarga e timeouts
@app.route('/api/upload', methods=['POST'])
def upload_file_and_save_to_firestore():
    # VERIFICAÇÃO: Checa se houve erro na inicialização
    if FIREBASE_INIT_ERROR:
        return jsonify({"error": FIREBASE_INIT_ERROR}), 500
        
    if 'dataFile' not in request.files: return jsonify({"error": "Nenhum arquivo enviado"}), 400
    file = request.files['dataFile']
    if file.filename == '': return jsonify({"error": "Nome de arquivo inválido"}), 400

    try:
        # OTIMIZAÇÃO: Usa um TextIOWrapper para ler o arquivo como um stream de texto
        text_stream = io.TextIOWrapper(file.stream, encoding='utf-8', errors='ignore')
        records = parse_data_file(text_stream)
        
        if not records: return jsonify({"error": "Nenhum registro válido encontrado"}), 400

        files_collection = db.collection(u'files')
        file_doc_ref = files_collection.document()
        file_doc_ref.set({
            'fileName': file.filename,
            'uploadTimestamp': firestore.SERVER_TIMESTAMP,
            'recordCount': len(records)
        })
        file_id = file_doc_ref.id

        records_collection = db.collection(u'flight_records')
        batch = db.batch()
        
        # OTIMIZAÇÃO: Salva os registros em lotes de 499 para não exceder os limites do Firebase
        for i, record in enumerate(records):
            record['fileId'] = file_id
            doc_ref = records_collection.document()
            batch.set(doc_ref, record)
            if (i + 1) % 499 == 0:
                batch.commit()
                batch = db.batch() # Inicia um novo lote
        
        batch.commit() # Salva o lote final com os registros restantes
        
        return jsonify({"success": f"{len(records)} registros de '{file.filename}' foram salvos."})
    except Exception as e:
        return jsonify({"error": f"Erro ao processar e salvar o arquivo: {str(e)}"}), 500

# Busca registros de voo filtrando por arquivo
@app.route('/api/flights', methods=['GET'])
def get_flights_from_firestore():
    # VERIFICAÇÃO: Checa se houve erro na inicialização
    if FIREBASE_INIT_ERROR:
        return jsonify({"error": FIREBASE_INIT_ERROR}), 500
        
    try:
        file_id = request.args.get('fileId')
        start_date_str = request.args.get('startDate')
        end_date_str = request.args.get('endDate')
        filter_value = request.args.get('filterValue', '').strip().upper()

        if not file_id:
            return jsonify([]) 

        query = db.collection(u'flight_records').where(u'fileId', u'==', file_id)

        if start_date_str:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            query = query.where(u'timestamp', u'>=', start_date)
        
        if end_date_str:
            # CORREÇÃO: Corrigido o formato da data de '%Y-m-%d' para '%Y-%m-%d'
            end_date = datetime.strptime(end_date_str + ' 23:59:59', '%Y-%m-%d %H:%M:%S')
            query = query.where(u'timestamp', u'<=', end_date)
        
        docs = query.stream()
        
        all_flights = []
        for doc in docs:
            flight_data = doc.to_dict()
            if 'timestamp' in flight_data and isinstance(flight_data.get('timestamp'), datetime):
                flight_data['timestamp'] = flight_data['timestamp'].isoformat() + 'Z'
            all_flights.append(flight_data)
        
        if filter_value:
            flights = [f for f in all_flights if 
                filter_value in f.get('matricula', '').upper() or
                filter_value in f.get('destino', '').upper() or
                filter_value in f.get('tipo_aeronave', '').upper()
            ]
        else:
            flights = all_flights

        flights.sort(key=lambda x: x.get('timestamp') or '', reverse=True)
        return jsonify(flights)
    except Exception as e:
        return jsonify({"error": f"Erro ao buscar dados do Firebase: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)

