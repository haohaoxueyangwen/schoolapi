from flask import Blueprint, jsonify, g, current_app

from config import model_registry

models_bp = Blueprint('models', __name__)


@models_bp.route('/v1/models', methods=['GET'])
def list_models():
    token = g.get("token", "")
    config = current_app.config.get("APP_CONFIG")
    models_map = model_registry.get_models(token, _config=config)

    models = []
    for model_id, info in models_map.items():
        models.append({
            "id": model_id,
            "object": "model",
            "owned_by": info.root_ai_type,
            "permission": []
        })
    return jsonify({"object": "list", "data": models})
