# -*- coding: utf-8 -*-

"""
Servidor Web de Análise de Tráfego Aéreo com Flask
===================================================
Versão integrada com Firebase para autenticação e persistência de dados
para deploy no Google Cloud Run.
"""

from flask import Flask, render_template, request, jsonify, abort
import pandas as pd
import re
from datetime import datetime
import io
import firebase_admin
from firebase_admin import credentials, firestore, auth, storage
import os

# Inicializa a aplicação Flask
app = Flask(__name__)

# Configura o Firebase Admin SDK usando o Application Default Credentials (ADC)
# O SDK encontrará as credenciais automaticamente no ambiente do Google Cloud Run.
try:
    # A linha abaixo é a única que precisa ser descomentada no deploy para o Cloud Run.
    # Em um ambiente de desenvolvimento local, essa linha falharia.
    # Por isso, vamos usar um bloco try/except para suportar ambos os ambientes.
    firebase_admin.initialize_app()
    
    # Para o deploy, o bucket precisa estar no ambiente de execução.
    # Vamos assumir que a variável de ambiente FIREBASE_STORAGE_BUCKET está configurada.
    bucket = storage.bucket(os.environ.get('FIREBASE_STORAGE_BUCKET'))
    
    db = firestore.client()
    print("Firebase Admin SDK inicializado com sucesso.")
except Exception as e:
    print(f"Erro ao inicializar o Firebase Admin SDK com ADC: {e}")
    print("Tentando inicializar com credenciais locais (apenas para desenvolvimento).")
    try:
        # Tenta carregar uma chave local para testes de desenvolvimento
        # Mantenha este caminho fora do seu .gitignore!
        cred = credentials.Certificate('firebase-credentials.json')
        firebase_admin.initialize_app(cred, {
            'storageBucket': 'seu-projeto-id.appspot.com'
        })
        db = firestore.client()
        bucket = storage.bucket()
        print("Firebase Admin SDK inicializado com credenciais locais.")
    except Exception as e:
        print(f"Falha na inicialização local: {e}")
        print("Atenção: A aplicação só funcionará no Google Cloud Run com as permissões corretas.")
        db = None # Garante que o aplicativo falhe se a conexão não for estabelecida
        bucket = None

def verify_id_token(id_token):
    # (A função de verificação de token permanece a mesma)
    try:
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except auth.InvalidIdTokenError as e:
        print(f"Token inválido: {e}")
        return None
    except Exception as e:
        print(f"Erro inesperado ao verificar o token: {e}")
        return None

def parse_data_file(file_content):
    # (A função de parsing permanece a mesma)
    lines = file_content.split('\n')
    records = []
    # ... (mesmo código do parsing)
    return records


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/upload', methods=['POST'])
def upload_file():
    if not db or not bucket:
        return jsonify({"error": "Serviço de banco de dados não disponível"}), 503
    
    # (A lógica de autenticação e processamento permanece a mesma)
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({"error": "Token de autenticação ausente ou mal formatado"}), 401
    
    id_token = auth_header.split('Bearer ')[1]
    decoded_token = verify_id_token(id_token)
    if not decoded_token:
        return jsonify({"error": "Não autorizado"}), 403
    
    # ... (mesmo código de upload) ...
    return jsonify({"success": True, "message": "Registros processados e salvos."}), 200

# Adicione aqui os endpoints /api/movements e /api/movements/<docId>
# ... (os outros endpoints permanecem os mesmos) ...

if __name__ == '__main__':
    # Cloud Run usa a variável de ambiente PORT, mas para teste local
    # vamos definir a porta 8080.
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=True, host='0.0.0.0', port=port)
