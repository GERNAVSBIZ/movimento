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

Bibliotecas necessárias:
--------------------
- Flask: Para criar o servidor web.
- pandas: Para manipulação e análise de dados.

Como instalar as bibliotecas (execute no seu terminal):
------------------------------------------------------
pip install Flask pandas

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
            # Extrai o operador (última palavra na linha)
            operator_match = re.search(r'\S+$', line.strip())
            if operator_match:
                record['responsavel'] = operator_match.group(0)

            # Extrai matrícula e tipo de aeronave (posições fixas)
            record['matricula'] = line[15:22].strip()
            record['tipo_aeronave'] = line[22:27].strip()

            # Encontra a regra de voo para ancorar a análise
            rule_match = re.search(r'(IV|VV)', line)
            if rule_match:
                record['regra_voo'] = rule_match.group(0).replace('IV', 'IFR').replace('VV', 'VFR')
                rule_index = rule_match.start()
                
                # Encontra a pista após a regra de voo
                string_after_rule = line[rule_index + 2:]
                pista_match = re.search(r'(\d{2})', string_after_rule)
                record['pista'] = pista_match.group(1) if pista_match else ''
                
                # Encontra o horário (último bloco de 4 dígitos antes da regra)
                string_before_rule = line[:rule_index]
                time_matches = re.findall(r'\d{4}', string_before_rule)
                if time_matches:
                    horario_str = time_matches[-1]
                    time_index = string_before_rule.rfind(horario_str)
                    record['destino'] = line[27:time_index].strip() or 'N/A'
                    
                    # Tenta processar a data/hora. Se falhar, timestamp continua None.
                    try:
                        data_str = line[9:15]
                        full_datetime_str = f"{data_str}{horario_str}"
                        dt_obj = datetime.strptime(full_datetime_str, '%d%m%y%H%M')
                        record['timestamp'] = dt_obj.isoformat() + 'Z'
                    except (ValueError, IndexError):
                        # Data inválida, mas o resto dos dados é mantido
                        pass
            
            records.append(record)

        except Exception as e:
            # Captura qualquer outro erro inesperado no parsing da linha
            # e adiciona o registro com os dados parciais que conseguiu obter
            print(f"Erro inesperado ao processar a linha: '{line.strip()}'. Erro: {e}")
            records.append(record)
    
    return records

@app.route('/')
def index():
    """ Rota principal que renderiza a página HTML. """
    return render_template('index.html')

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """
    Rota da API para receber o arquivo, processá-lo com pandas
    e retornar os dados como JSON.
    """
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

        df = pd.DataFrame(records)
        json_data = df.to_json(orient='records', date_format='iso')
        
        return json_data

    except Exception as e:
        return jsonify({"error": f"Erro ao processar o arquivo: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)

