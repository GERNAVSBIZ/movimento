# -*- coding: utf-8 -*-

"""
Servidor Web de Análise de Tráfego Aéreo com Flask
===================================================

Este script utiliza o framework Flask para criar um backend (servidor)
que processa arquivos de tráfego aéreo e os disponibiliza para uma
interface web (frontend).

Funcionalidades do Backend:
- Servir a página principal `index.html`.
- Receber uploads de arquivos .dat via uma API.
- Utilizar a biblioteca `pandas` para analisar os dados do arquivo.
- Retornar os dados processados em formato JSON para o frontend.
- Conectar-se ao Firebase Firestore para salvar e buscar dados.

Bibliotecas necessárias:
--------------------
- Flask: Para criar o servidor web.
- pandas: Para manipulação e análise de dados.
- firebase-admin: Para interagir com o Firebase.

Como instalar as bibliotecas (execute no seu terminal):
------------------------------------------------------
pip install -r requirements.txt

Como executar o servidor:
-------------------------
1. Salve este arquivo como `app.py`.
2. Salve o arquivo HTML na mesma pasta, dentro de uma subpasta chamada `templates`.
3. Abra o terminal na pasta principal e execute: python app.py
4. Acesse http://127.0.0.1:5000 no seu navegador.
"""

from flask import Flask, render_template, request, jsonify
import pandas as pd
import re
from datetime import datetime
import io
import firebase_admin
from firebase_admin import credentials, firestore

# Inicializa a aplicação Flask
app = Flask(__name__)

# Inicializa Firebase Admin SDK com sua chave de serviço
# O arquivo de chave JSON é referenciado aqui. Certifique-se de que ele está no diretório correto.
try:
    cred = credentials.Certificate("movimento-aeronaves-firebase-adminsdk-fbsvc-78e62bb66c.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebase inicializado com sucesso!")
except Exception as e:
    print(f"Erro ao inicializar o Firebase: {e}")
    db = None

def parse_data_file(file_content):
    """
    Analisa o conteúdo de um arquivo de dados e extrai os registros de voo.
    Se uma data for inválida, o campo 'timestamp' ficará nulo, mas o resto
    da linha será processado e incluído no resultado.
    """
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
                        record['timestamp'] = dt_obj.isoformat() + 'Z'
                    except (ValueError, IndexError):
                        pass
            
            records.append(record)

        except Exception as e:
            print(f"Erro inesperado ao processar a linha: '{line.strip()}'. Erro: {e}")
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
        # Converte timestamp para um objeto de data/hora do Firestore
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
        # Converte timestamp do Firestore de volta para string ISO
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
        content = io.StringIO(file.stream.read().decode("utf-8", errors='ignore')).getvalue()
        records = parse_data_file(content)
        
        if not records:
            return jsonify({"error": "Nenhum registro válido encontrado no arquivo"}), 400
        
        # Salva os registros no Firebase
        save_result, status_code = save_to_firebase(records)
        if status_code != 200:
            return jsonify(save_result), status_code

        return jsonify({"message": f"{len(records)} registros processados e salvos.", "uploadedCount": len(records)})

    except Exception as e:
        return jsonify({"error": f"Erro ao processar o arquivo: {str(e)}"}), 500
        
@app.route('/api/fetch_all', methods=['GET'])
def fetch_all_data():
    """ Nova rota da API para buscar todos os dados do Firestore. """
    result, status_code = fetch_from_firebase()
    return jsonify(result), status_code

if __name__ == '__main__':
    app.run(debug=True)
