# Простое Flask-приложение. numpy импортируется, чтобы в куче каждого
# worker-а было много одинаковых объектов модулей (типичный случай
# Python-сервисов). Таблица 200 тыс. строк имитирует данные приложения,
# одинаковые у всех экземпляров (справочники, конфиги).
import numpy as np
from flask import Flask, jsonify

app = Flask(__name__)
TABLE = {f"key-{i}": f"value-{i}" for i in range(200_000)}
ARR = np.arange(1_000_000, dtype=np.float64)


@app.route("/")
def index():
    return jsonify(n=len(TABLE), s=float(ARR[:1000].sum()))
