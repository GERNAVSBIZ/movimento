# -*- coding: utf-8 -*-

"""
Servidor Web de Análise de Tráfego Aéreo com Flask e Firebase
=============================================================

Este script atualiza o backend para integrar autenticação e persistência de dados
usando o Firebase.

- **Firebase Admin SDK**: Utilizado para verificar tokens de autenticação
  e interagir com o Cloud Firestore e Firebase Storage.
- **Autenticação Segura**: Recebe e valida tokens de ID em cada requisição.
- **Persistência**: Dados de voo são salvos em Cloud Firestore e os arquivos
  brutos são opcionalmente armazenados em Firebase Storage.
"""

from flask import Flask, render_template, request, jsonify, abort
import pandas as pd
import re
from datetime import datetime
import io
import firebase_admin
from firebase_admin import credentials, auth, firestore, storage
import os
import base64
import uuid

# Inicializa a aplicação Flask
app = Flask(__name__, template_folder='.')

# Inicializa o Firebase Admin SDK
try:
    # A credencial é lida de uma variável de ambiente codificada em Base64
    cred_json_str = base64.b64decode(os.environ.get('FIREBASE_ADMIN_CREDENTIALS')).decode('utf-8')
    cred = credentials.Certificate(io.StringIO(cred_json_str))
    firebase_admin.initialize_app(cred, {
        'storageBucket': 'movimento-aeronaves.appspot.com'
    })
    db = firestore.client()
    bucket = storage.bucket()
    print("Firebase Admin SDK inicializado com sucesso.")
except Exception as e:
    print(f"Erro ao inicializar o Firebase Admin SDK: {e}")
    # Em um ambiente de produção, você pode considerar abortar a inicialização.

def verify_id_token(id_token):
    """
    Verifica o ID Token do Firebase e retorna os dados do usuário.
    """
    try:
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except auth.InvalidIdTokenError:
        return None
    except Exception as e:
        print(f"Erro na verificação do token: {e}")
        return None

def parse_data_file(file_content):
    """
    Analisa o conteúdo de um arquivo de dados e extrai os registros de voo.
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
    """Rota principal que renderiza a página HTML."""
    return render_template('index.html')

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """
    Endpoint da API para receber um arquivo .dat, processá-lo e salvar os registros no Firestore
    e o arquivo bruto no Storage. Requer autenticação.
    """
    id_token = request.headers.get('Authorization', '').split('Bearer ')[-1]
    if not id_token:
        return jsonify({"error": "Token de autenticação não fornecido"}), 401
    
    user = verify_id_token(id_token)
    if not user:
        return jsonify({"error": "Token de autenticação inválido ou expirado"}), 401

    if 'dataFile' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    
    file = request.files['dataFile']
    
    if file.filename == '':
        return jsonify({"error": "Nome de arquivo inválido"}), 400

    try:
        file_content_raw = file.stream.read()
        file_content_str = io.StringIO(file_content_raw.decode("utf-8", errors='ignore')).getvalue()
        records = parse_data_file(file_content_str)
        
        if not records:
            return jsonify({"error": "Nenhum registro válido encontrado no arquivo"}), 400

        batch = db.batch()
        upload_date = datetime.utcnow().isoformat() + 'Z'
        doc_ref = db.collection('uploads').document()
        doc_id = doc_ref.id

        batch.set(doc_ref, {
            'filename': file.filename,
            'ownerUid': user['uid'],
            'uploadDate': upload_date,
            'processedRecords': len(records),
            'status': 'processing'
        })
        
        for record in records:
            record['ownerUid'] = user['uid']
            record['uploadId'] = doc_id
            record['processedAt'] = firestore.SERVER_TIMESTAMP
            batch.set(db.collection('movements').document(), record)

        blob = bucket.blob(f"raw_uploads/{doc_id}_{file.filename}")
        blob.upload_from_string(file_content_raw, content_type=file.content_type)
        
        batch.update(doc_ref, {
            'status': 'completed',
            'storageUrl': blob.public_url
        })
        
        batch.commit()
        
        return jsonify({
            "message": "Dados processados e salvos com sucesso!",
            "recordCount": len(records),
            "uploadId": doc_id
        }), 200

    except Exception as e:
        print(f"Erro ao processar o arquivo: {str(e)}")
        if 'doc_ref' in locals():
            doc_ref.update({'status': 'failed', 'error': str(e)})
        return jsonify({"error": f"Erro interno ao processar o arquivo: {str(e)}"}), 500

@app.route('/api/movements/<doc_id>', methods=['DELETE'])
def delete_movement(doc_id):
    """
    Endpoint da API para excluir um registro de movimento.
    Requer autenticação. O usuário deve ser o proprietário ou um administrador.
    """
    id_token = request.headers.get('Authorization', '').split('Bearer ')[-1]
    if not id_token:
        return jsonify({"error": "Token de autenticação não fornecido"}), 401

    user = verify_id_token(id_token)
    if not user:
        return jsonify({"error": "Token de autenticação inválido ou expirado"}), 401

    try:
        doc_ref = db.collection('movements').document(doc_id)
        doc = doc_ref.get()

        if not doc.exists:
            return jsonify({"message": "Documento não encontrado."}), 404

        data = doc.to_dict()
        owner_uid = data.get('ownerUid')
        
        is_admin = user.get('admin', False)
        
        if user['uid'] == owner_uid or is_admin:
            doc_ref.delete()
            return jsonify({"message": "Documento excluído com sucesso."}), 200
        else:
            return jsonify({"error": "Acesso negado. Você não tem permissão para excluir este documento."}), 403

    except Exception as e:
        return jsonify({"error": f"Erro ao excluir o documento: {str(e)}"}), 500

@app.route('/api/movements', methods=['GET'])
def get_movements():
    """
    Endpoint da API para buscar documentos de movimentos.
    Requer autenticação.
    """
    id_token = request.headers.get('Authorization', '').split('Bearer ')[-1]
    if not id_token:
        return jsonify({"error": "Token de autenticação não fornecido"}), 401
    
    user = verify_id_token(id_token)
    if not user:
        return jsonify({"error": "Token de autenticação inválido ou expirado"}), 401
        
    try:
        query = db.collection('movements').where('ownerUid', '==', user['uid'])
        docs = query.stream()
        
        results = []
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id
            results.append(data)
            
        return jsonify(results), 200
    
    except Exception as e:
        return jsonify({"error": f"Erro ao buscar documentos: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)
