# -*- coding: utf-8 -*-
import os
import io
import re
from datetime import datetime
from flask import Flask, render_template, request, jsonify
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore

# --- INICIALIZAÇÃO DO FIREBASE ---
# Carrega as credenciais do Firebase a partir do arquivo JSON.
# Garanta que o arquivo 'firebase-credentials.json' esteja na mesma pasta.
try:
    cred = credentials.Certificate("firebase-credentials.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebase conectado com sucesso!")
except Exception as e:
    print(f"Erro ao conectar com o Firebase: {e}")
    db = None

# Inicializa a aplicação Flask
app = Flask(__name__)

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

@app.route('/')
def index():
    """ Rota principal que renderiza a página HTML. """
    return render_template('index.html')

@app.route('/api/upload', methods=['POST'])
def upload_and_process_file():
    """
    Recebe o arquivo, processa e salva cada registro no Firestore.
    """
    if not db:
        return jsonify({"error": "Conexão com o Firebase não estabelecida."}), 500
        
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

        # Salva cada registro no Firestore
        batch = db.batch()
        collection_ref = db.collection('movimento_aeronaves')
        count = 0
        for record in records:
            # Usar a matrícula e o timestamp (se disponível) para criar um ID único
            doc_id = f"{record.get('matricula', 'NA')}_{record.get('timestamp', datetime.now().isoformat())}"
            doc_ref = collection_ref.document(doc_id)
            batch.set(doc_ref, record)
            count += 1
        
        batch.commit()
        
        return jsonify({"message": f"{count} registros salvos com sucesso no Firebase!"}), 200

    except Exception as e:
        return jsonify({"error": f"Erro ao processar e salvar o arquivo: {str(e)}"}), 500

@app.route('/api/data', methods=['GET'])
def fetch_from_firebase():
    """
    Busca todos os registros da coleção 'movimento_aeronaves' no Firestore.
    """
    if not db:
        return jsonify({"error": "Conexão com o Firebase não estabelecida."}), 500

    try:
        docs = db.collection('movimento_aeronaves').stream()
        records = [doc.to_dict() for doc in docs]
        
        # Converte para um DataFrame do Pandas e depois para JSON para garantir consistência
        df = pd.DataFrame(records)
        json_data = df.to_json(orient='records', date_format='iso')
        
        return json_data
        
    except Exception as e:
        return jsonify({"error": f"Erro ao buscar dados do Firebase: {str(e)}"}), 500

if __name__ == '__main__':
    # Define a porta a partir de uma variável de ambiente, com um padrão para desenvolvimento local
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
