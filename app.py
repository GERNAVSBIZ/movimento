# -*- coding: utf-8 -*-

"""
Servidor Web de Análise de Tráfego Aéreo com Flask
===================================================

- Processa arquivos .dat de tráfego aéreo
- Salva os registros no Firestore (Firebase)
- Disponibiliza API para buscar dados
- Interface HTML no / (templates/index.html)
"""

from flask import Flask, render_template, request, jsonify
import pandas as pd
import re
from datetime import datetime
import io
import firebase_admin
from firebase_admin import credentials, firestore
import json
import os

# Inicializa a aplicação Flask
app = Flask(__name__)

# Inicializa Firebase Admin SDK
try:
    credentials_json_str = os.environ.get('FIREBASE_CREDENTIALS')
    if credentials_json_str:
        # Lê direto da variável de ambiente (Render)
        cred = credentials.Certificate.from_service_account_info(json.loads(credentials_json_str))
        print("✅ Credenciais do Firebase lidas da variável de ambiente.")
    else:
        # Fallback para arquivo local (desenvolvimento)
        cred = credentials.Certificate("movimento-aeronaves-firebase-adminsdk-fbsvc-78e62bb66c.json")
        print("⚠️ Credenciais do Firebase lidas do arquivo local.")

    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("🔥 Firebase inicializado com sucesso!")
except Exception as e:
    print(f"❌ Erro ao inicializar o Firebase: {e}")
    db = None


def parse_data_file_line_by_line(file_stream):
    """
    Analisa o conteúdo de um arquivo de dados lendo linha por linha.
    """
    records = []
    file_content_stream = io.TextIOWrapper(file_stream, encoding='utf-8', errors='ignore')

    for line in file_content_stream:
        line_stripped = line.strip()
        if len(line_stripped) <= 50 or line_stripped.startswith('SBIZAIZ0'):
            continue

        record = {
            'timestamp': None, 'matricula': 'N/A', 'tipo_aeronave': 'N/A',
            'destino': 'N/A', 'regra_voo': 'N/A', 'pista': '', 'responsavel': 'N/A'
        }

        try:
            operator_match = re.search(r'\S+$', line_stripped)
            if operator_match:
                record['responsavel'] = operator_match.group(0)

            record['matricula'] = line_stripped[15:22].strip()
            record['tipo_aeronave'] = line_stripped[22:27].strip()

            rule_match = re.search(r'(IV|VV)', line_stripped)
            if rule_match:
                record['regra_voo'] = rule_match.group(0).replace('IV', 'IFR').replace('VV', 'VFR')
                rule_index = rule_match.start()

                string_after_rule = line_stripped[rule_index + 2:]
                pista_match = re.search(r'(\d{2})', string_after_rule)
                record['pista'] = pista_match.group(1) if pista_match else ''

                string_before_rule = line_stripped[:rule_index]
                time_matches = re.findall(r'\d{4}', string_before_rule)
                if time_matches:
                    horario_str = time_matches[-1]
                    time_index = string_before_rule.rfind(horario_str)
                    record['destino'] = line_stripped[27:time_index].strip() or 'N/A'

                    try:
                        data_str = line_stripped[9:15]
                        full_datetime_str = f"{data_str}{horario_str}"
                        dt_obj = datetime.strptime(full_datetime_str, '%d%m%y%H%M')
                        record['timestamp'] = dt_obj.isoformat() + 'Z'
                    except (ValueError, IndexError):
                        pass

            records.append(record)

        except Exception as e:
            print(f"⚠️ Erro inesperado ao processar linha: '{line_stripped}'. Erro: {e}")
            records.append(record)

    return records


def save_to_firebase(records):
    """ Salva os registros no Firestore. """
    if not db:
        return {"error": "Firebase não inicializado"}, 500

    batch = db.batch()
    collection_ref = db.collection('movimento_aeronaves')
    count = 0

    for record in records:
        doc_ref = collection_ref.document()
        if record['timestamp']:
            record['timestamp'] = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00'))
        batch.set(doc_ref, record)
        count += 1

    batch.commit()
    return {"message": f"{count} registros salvos no Firestore"}, 200


def fetch_from_firebase():
    """ Busca todos os registros da coleção 'movimento_aeronaves' no Firestore. """
    if not db:
        return {"error": "Firebase não inicializado"}, 500

    records = []
    docs = db.collection('movimento_aeronaves').stream()
    for doc in docs:
        record = doc.to_dict()
        if isinstance(record.get('timestamp'), datetime):
            record['timestamp'] = record['timestamp'].isoformat() + 'Z'
        records.append(record)

    return {"data": records}, 200


@app.route('/')
def index():
    """ Rota principal que renderiza a página HTML. """
    return render_template('index.html')


@app.route('/api/upload', methods=['POST'])
def upload_file():
    """ Rota da API para processar e salvar o arquivo no Firestore. """
    if 'dataFile' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    file = request.files['dataFile']
    if file.filename == '':
        return jsonify({"error": "Nome de arquivo inválido"}), 400

    try:
        records = parse_data_file_line_by_line(file.stream)
        if not records:
            return jsonify({"error": "Nenhum registro válido encontrado no arquivo"}), 400

        save_result, status_code = save_to_firebase(records)
        if status_code != 200:
            return jsonify(save_result), status_code

        return jsonify({"message": f"{len(records)} registros processados e salvos.", "uploadedCount": len(records)})

    except Exception as e:
        return jsonify({"error": f"Erro ao processar o arquivo: {str(e)}"}), 500


@app.route('/api/fetch_all', methods=['GET'])
def fetch_all_data():
    """ API para buscar todos os dados do Firestore. """
    result, status_code = fetch_from_firebase()
    return jsonify(result), status_code


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
