# -*- coding: utf-8 -*-

from flask import Flask, render_template, request, jsonify
import pandas as pd
import re
from datetime import datetime
import io

# --- Novas importações do Firebase ---
import firebase_admin
from firebase_admin import credentials, firestore

# --- Inicialização do Firebase Admin SDK ---
try:
    cred = credentials.Certificate("firebase-credentials.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    print(f"ERRO: Não foi possível inicializar o Firebase. Verifique se o arquivo 'firebase-credentials.json' está na pasta. Detalhes: {e}")
    db = None

app = Flask(__name__)

def parse_data_file(file_content):
    lines = file_content.split('\n')
    records = []
    
    for line in lines:
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
    if not db:
        return jsonify({"error": "A conexão com o banco de dados não foi inicializada."}), 500
    try:
        files_ref = db.collection(u'files').order_by(u'uploadTimestamp', direction=firestore.Query.DESCENDING)
        docs = files_ref.stream()
        file_list = []
        # CORREÇÃO: A linha abaixo estava com um erro de digitação ('for doc in doc').
        for doc in docs:
            doc_data = doc.to_dict()
            if 'uploadTimestamp' in doc_data and doc_data['uploadTimestamp']:
                 doc_data['uploadTimestamp'] = doc_data['uploadTimestamp'].isoformat() + 'Z'
            file_list.append({"id": doc.id, "data": doc_data})

        return jsonify(file_list)
    except Exception as e:
        return jsonify({"error": f"Erro ao buscar lista de arquivos: {str(e)}"}), 500

# Salva o arquivo e os registros associados
@app.route('/api/upload', methods=['POST'])
def upload_file_and_save_to_firestore():
    if not db:
        return jsonify({"error": "A conexão com o banco de dados Firebase não foi inicializada."}), 500
        
    if 'dataFile' not in request.files: return jsonify({"error": "Nenhum arquivo enviado"}), 400
    file = request.files['dataFile']
    if file.filename == '': return jsonify({"error": "Nome de arquivo inválido"}), 400

    try:
        content = io.StringIO(file.stream.read().decode("utf-8", errors='ignore')).getvalue()
        records = parse_data_file(content)
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
        for record in records:
            record['fileId'] = file_id
            doc_ref = records_collection.document()
            batch.set(doc_ref, record)
        batch.commit()
        
        return jsonify({"success": f"{len(records)} registros de '{file.filename}' foram salvos."})
    except Exception as e:
        return jsonify({"error": f"Erro ao processar e salvar o arquivo: {str(e)}"}), 500

# Busca registros de voo filtrando por arquivo
@app.route('/api/flights', methods=['GET'])
def get_flights_from_firestore():
    if not db: return jsonify({"error": "A conexão com o banco de dados não foi inicializada."}), 500
        
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
            end_date = datetime.strptime(end_date_str + ' 23:59:59', '%Y-%m-%d %H:%M:%S')
            query = query.where(u'timestamp', u'<=', end_date)
        
        docs = query.stream()
        
        all_flights = []
        for doc in docs:
            flight_data = doc.to_dict()
            if 'timestamp' in flight_data and flight_data['timestamp']:
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

