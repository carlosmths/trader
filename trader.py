import keyboard
import pygame
import time
from binance.client import Client
from binance.enums import ORDER_TYPE_MARKET, FUTURE_ORDER_TYPE_STOP_MARKET, FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET, SIDE_BUY, SIDE_SELL
import json

# Cargar configuración desde un archivo JSON
with open("config.json") as f:
    config = json.load(f)

TESTNET = config.get("testnet", False)
API_KEY = config["api_key_testnet"] if TESTNET else config["api_key"]
API_SECRET = config["api_secret_testnet"] if TESTNET else config["api_secret"]
SYMBOL = config["symbol"]
CAPITAL_PERCENTAGE = config["capital_percentage"] / 100
LEVERAGE = config["leverage"]
STOP_LOSS_PERCENT = config["stop_loss_pct"] / 100
TAKE_PROFIT_PERCENT = config["take_profit_pct"] / \
    100 if "take_profit_pct" in config else None
SOUND_ERROR = "error.mp3"
SOUND_SUCCESS = "success.mp3"

client = Client(API_KEY, API_SECRET, testnet=TESTNET)
client.futures_change_leverage(
    symbol=SYMBOL, leverage=LEVERAGE)\

pygame.mixer.init()


def get_available_margin():
    balance = client.futures_account_balance()
    usdt_balance = next(b for b in balance if b['asset'] == 'USDT')
    return float(usdt_balance['balance'])


def play_sound(filename):
    pygame.mixer.music.load(filename)
    pygame.mixer.music.play()


def check_status():
    try:
        if TESTNET:
            print("✅ TESTNET ACTIVO! Los trades no son con dinero real.")

        # Test de conexión general
        status = client.get_system_status()
        if status["status"] == 0:
            print("✅ Conexión a Binance establecida correctamente.")
        else:
            print("⚠️ Problema con Binance:", status)

        # Test de cuenta de Futuros
        balance = get_available_margin()
        print("✅ Acceso a Binance Futures confirmado.")
        print(
            f"✅ Saldo disponible en USDT: {balance}")

        # Test de obtener el precio del par de futuros elegido
        ticker = client.futures_symbol_ticker(symbol=SYMBOL)
        print(f"✅ Precio actual de {SYMBOL} en Futuros: {ticker['price']}")

    except Exception as e:
        print(f"❌ Error en la conexión: {e}")
        play_sound(SOUND_ERROR)


check_status()


def get_available_margin():
    """Obtiene el saldo disponible en USDT."""
    account_info = client.futures_account()
    return float(account_info["availableBalance"])


def get_trade_limits_and_precision():
    """Obtiene los límites de cantidad y la precisión de precio para operar el símbolo elegido."""
    exchange_info = client.futures_exchange_info()
    symbol_info = next(
        (s for s in exchange_info["symbols"] if s["symbol"] == SYMBOL), None)

    if not symbol_info:
        print(f"❌ No se pudo obtener información del símbolo {SYMBOL}")
        return None, None, None, None

    filters = {f["filterType"]: f for f in symbol_info["filters"]}

    min_qty = float(filters["LOT_SIZE"]["minQty"])
    max_qty = float(filters["LOT_SIZE"]["maxQty"])
    step_size = float(filters["LOT_SIZE"]["stepSize"])
    price_precision = len(filters["PRICE_FILTER"]["tickSize"].rstrip(
        '0').split('.')[-1])  # Decimales permitidos en precio

    return min_qty, max_qty, step_size, price_precision


# Obtener los valores al inicio del script
MIN_QTY, MAX_QTY, STEP_SIZE, PRICE_PRECISION = get_trade_limits_and_precision()


def get_quantity():
    """Calcula la cantidad correcta para la orden."""
    try:
        usdt_balance = get_available_margin()
        capital = usdt_balance * CAPITAL_PERCENTAGE * LEVERAGE
        price = float(client.futures_mark_price(symbol=SYMBOL)["markPrice"])
        quantity = capital / price

        # Ajustar cantidad al step size permitido
        quantity = max(
            min(round(quantity - (quantity % STEP_SIZE), 8), MAX_QTY), MIN_QTY)
        return quantity
    except Exception as e:
        print(f"❌ Error al calcular la cantidad: {e}")
        return 0


def wait_for_entry_price(order_id):
    """Espera hasta obtener un precio de entrada real."""
    max_retries = 10
    wait_time = 0.2

    for _ in range(max_retries):
        order_info = client.futures_get_order(symbol=SYMBOL, orderId=order_id)
        entry_price = float(order_info["avgPrice"])

        if entry_price > 0:
            # Redondear correctamente
            return round(entry_price, PRICE_PRECISION)

        time.sleep(wait_time)

    print("⚠️ No se pudo obtener el precio de entrada, usando precio de mercado.")
    return round(float(client.futures_mark_price(symbol=SYMBOL)["markPrice"]), PRICE_PRECISION)


def place_order(side):
    try:
        quantity = get_quantity()
        if quantity <= 0:
            print("⚠️ La cantidad calculada es menor al mínimo permitido.")
            return

        # Crear orden de mercado
        order = client.futures_create_order(
            symbol=SYMBOL,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
        print(f"✅ Orden ejecutada: {order}")

        entry_price = wait_for_entry_price(order["orderId"])

        # Colocar Stop Loss
        sl_price = round(entry_price * (1 - STOP_LOSS_PERCENT) if side ==
                         SIDE_BUY else entry_price * (1 + STOP_LOSS_PERCENT), PRICE_PRECISION)
        client.futures_create_order(
            symbol=SYMBOL,
            side=SIDE_SELL if side == SIDE_BUY else SIDE_BUY,
            type=FUTURE_ORDER_TYPE_STOP_MARKET,
            stopPrice=sl_price,
            closePosition=True
        )
        print(f"✅ Stop Loss colocado en {sl_price}")

        # Colocar Take Profit (si está configurado)
        if TAKE_PROFIT_PERCENT:
            tp_price = round(entry_price * (1 + TAKE_PROFIT_PERCENT) if side == SIDE_BUY else entry_price * (
                1 - TAKE_PROFIT_PERCENT), PRICE_PRECISION) if TAKE_PROFIT_PERCENT else None
            client.futures_create_order(
                symbol=SYMBOL,
                side=SIDE_SELL if side == SIDE_BUY else SIDE_BUY,
                type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                stopPrice=tp_price,
                closePosition=True
            )
            print(f"✅ Take Profit colocado en {tp_price}")

        play_sound(SOUND_SUCCESS)
    except Exception as e:
        print(f"❌ Error al ejecutar la orden: {e}")
        play_sound(SOUND_ERROR)


def close_position():
    try:
        # 2️⃣ Obtener la posición actual
        positions = client.futures_position_information()
        position = next((p for p in positions if p["symbol"] == SYMBOL), None)

        if position:
            position_size = float(position["positionAmt"])
            if position_size != 0:
                # 3️⃣ Determinar si la posición es LONG o SHORT y cerrarla
                # Si es long, vende. Si es short, compra.
                side = SIDE_SELL if position_size > 0 else SIDE_BUY
                client.futures_create_order(
                    symbol=SYMBOL,
                    side=side,
                    type=ORDER_TYPE_MARKET,
                    quantity=abs(position_size),
                    reduceOnly=True
                )
                print("✅ Posición cerrada exitosamente.")
                play_sound(SOUND_SUCCESS)
            else:
                print("⚠️ No hay posición abierta.")
        else:
            print(
                f"⚠️ No se encontró información de la posición para {SYMBOL}")

        # 1️⃣ Cancelar todas las órdenes abiertas (SL y TP)
        client.futures_cancel_all_open_orders(symbol=SYMBOL)
    except Exception as e:
        print(f"❌ Error al cerrar la posición: {e}")
        play_sound(SOUND_ERROR)


# Atajos de teclado
keyboard.add_hotkey("ctrl+alt+b", lambda: place_order(SIDE_BUY))  # Long
keyboard.add_hotkey("ctrl+alt+s", lambda: place_order(SIDE_SELL))  # Short
keyboard.add_hotkey("ctrl+alt+x", close_position)  # Cerrar posición

print("CUIDADO! Script activo. Usa CTRL+ALT+B para Buy, CTRL+ALT+S para Sell, CTRL+ALT+X para cerrar posición.")
keyboard.wait()
