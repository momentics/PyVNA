import logging
import re
import time
from threading import Lock
from urllib import request

#from flask import Flask, request, jsonify

from pyvna.device_manager import DeviceManager
from pyvna.data_types import SweepConfig

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
device_manager = DeviceManager()

# Валидация параметра порта. Разрешает /dev/ttyACM*, /dev/ttyUSB*, COM*
# Это критическая мера безопасности для предотвращения Path Traversal.
VALID_PORT_PATTERN = re.compile(r"^(COM\d+|/dev/tty(ACM|USB)\d+)$")

# Базовый Rate Limiting для предотвращения DoS-атак.
# Ограничивает 5 запросов в секунду на IP-адрес.
# Для более сложных сценариев следует использовать специализированные библиотеки.
RATE_LIMIT_INTERVAL = 1  # секунды
RATE_LIMIT_MAX_REQUESTS = 5
request_counts = {}
request_locks = {} # Мьютексы для каждого IP


@app.before_request
def before_request():
    """Реализация базового Rate Limiting."""
    client_ip = request.remote_addr
    
    if client_ip not in request_locks:
        request_locks[client_ip] = Lock()

    with request_locks[client_ip]:
        current_time = time.time()
        
        if client_ip not in request_counts:
            request_counts[client_ip] = {'timestamp': current_time, 'count': 0}

        # Сброс счетчика, если интервал прошел
        if current_time - request_counts[client_ip]['timestamp'] > RATE_LIMIT_INTERVAL:
            request_counts[client_ip]['timestamp'] = current_time
            request_counts[client_ip]['count'] = 0

        request_counts[client_ip]['count'] += 1

        if request_counts[client_ip]['count'] > RATE_LIMIT_MAX_REQUESTS:
            logging.warning(f"Rate limit exceeded for IP: {client_ip}")
            return jsonify({"error": "Too many requests. Please try again later."}), 429


@app.route('/api/v1/scan', methods=['GET'])
def scan_device():
    """
    Обрабатывает запросы на сканирование VNA устройства.
    """
    port = request.args.get('port')
    
    # Строгая валидация порта. Критично для безопасности.
    if not port or not VALID_PORT_PATTERN.match(port):
        logging.warning(f"Invalid or missing port parameter: {port}")
        return jsonify({"error": "Invalid or missing 'port' parameter."}), 400

    try:
        # Получаем устройство из пула. Если его нет, оно будет создано и идентифицировано.
        vna = device_manager.get_device(port)
        
        # Установка параметров сканирования.
        # В реальном приложении эти параметры можно брать из запроса после валидации.
        # MAX_SWEEP_POINTS ограничение задано в data_types.py
        sweep_config = SweepConfig(start=1_000_000, stop=900_000_000, points=101) # 1MHz to 900MHz, 101 points
        vna.set_sweep(sweep_config)

        # Выполнение сканирования и измерение времени
        start_time = time.time()
        vna_data = vna.get_data()
        end_time = time.time()
        logging.info(f"Scan on {port} completed in {end_time - start_time:.2f} seconds.")

        # Возвращаем данные в формате Touchstone
        return vna_data.to_touchstone(), 200

    except ValueError as e:
        # Ошибки, связанные с некорректным вводом (например, SetSweep валидация)
        logging.error(f"Validation error for {port}: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        # Общие ошибки устройства или системы.
        # Важно: не раскрываем детали ошибки клиенту! Логируем полностью.
        logging.error(f"Error accessing or scanning device {port}: {e}", exc_info=True)
        return jsonify({"error": "Internal server error: Could not process request for device."}), 500

if __name__ == '__main__':
    # Flask запускается в режиме разработки. Для production использовать WSGI-сервер (Gunicorn, uWSGI).
    # Запускается на 0.0.0.0, чтобы быть доступным из Docker.
    logging.info("Starting PyVNA Flask server...")
    app.run(host='0.0.0.0', port=5000, debug=False)
    logging.info("PyVNA Flask server stopped.")
