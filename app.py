import os
from flask import Flask, jsonify
from flask_cors import CORS
from flasgger import Swagger

from helpers import FRONTEND_ORIGINS
from routes.auth_routes import auth_bp
from routes.admin_routes import admin_bp
from routes.eventos_routes import eventos_bp
from routes.imagens_routes import imagens_bp

# Inicializa o Flask
app = Flask(__name__)
CORS(app, origins=FRONTEND_ORIGINS, supports_credentials=True)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "chronohistory_secret_2025")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("SESSION_COOKIE_SAMESITE", "None")
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "true").lower() == "true"

# Versão do OPEN API
app.config['SWAGGER'] = {
    'openapi': '3.0.0'
}
# Chamar o OPENAPI para o código
swagger = Swagger(app, template_file='openapi.yaml')

# ─── Blueprints: cada arquivo em routes/ cuida de um recurso ────────────────
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(eventos_bp)
app.register_blueprint(imagens_bp)


#===============================
# Rotas de tratamento de erros
#===============================

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "status": "error",
        "message": "Página não encontrada",
        "data": str(error)
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "status": "error",
        "message": "Erro interno do servidor",
        "data": str(error)
    }), 500

# Executa o servidor local
if __name__ == "__main__":
    app.run(debug=True)
