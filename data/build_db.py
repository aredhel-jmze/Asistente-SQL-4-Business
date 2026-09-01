"""
Genera business.db: una base de datos SQLite sintética de ventas e
inventario, con seis meses de datos (marzo a agosto 2026).

Uso:
    python build_db.py
"""
import sqlite3
import random
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "business.db"
random.seed(42)

PRODUCTS = [
    (1, "Audifonos Inalambricos", "Electronica", 29990),
    (2, "Notebook 14 pulgadas", "Electronica", 549990),
    (3, "Mouse Optico", "Electronica", 8990),
    (4, "Juego de Sabanas", "Hogar", 24990),
    (5, "Set de Ollas", "Hogar", 59990),
    (6, "Lampara de Escritorio", "Hogar", 15990),
    (7, "Polera Basica", "Ropa", 9990),
    (8, "Zapatillas Urbanas", "Ropa", 39990),
    (9, "Cafe Molido 500g", "Alimentos", 6990),
    (10, "Aceite de Oliva 500ml", "Alimentos", 7990),
]

START = date(2026, 3, 1)
END = date(2026, 8, 31)


def daterange(start, end):
    days = (end - start).days
    for i in range(days + 1):
        yield start + timedelta(days=i)


def build():
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price INTEGER NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE sales (
            sale_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            sale_date TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(product_id)
        )
    """)
    cur.execute("""
        CREATE TABLE inventory (
            product_id INTEGER PRIMARY KEY,
            stock INTEGER NOT NULL,
            reorder_point INTEGER NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(product_id)
        )
    """)

    cur.executemany(
        "INSERT INTO products VALUES (?, ?, ?, ?)", PRODUCTS
    )

    # Ventas: cada dia, entre 0 y 3 ventas de productos aleatorios.
    # Se sesga a mas ventas en el segundo trimestre del semestre
    # (junio a agosto) para que el crecimiento semestral sea real
    # y no un artefacto de ruido puro.
    sales_rows = []
    for d in daterange(START, END):
        q_boost = 1.6 if d >= date(2026, 6, 1) else 1.0
        n_sales = int(random.randint(1, 4) * q_boost)
        for _ in range(n_sales):
            pid, _, _, price = random.choice(PRODUCTS)
            qty = random.randint(1, 5)
            sales_rows.append((pid, d.isoformat(), qty, qty * price))

    cur.executemany(
        "INSERT INTO sales (product_id, sale_date, quantity, amount) "
        "VALUES (?, ?, ?, ?)",
        sales_rows,
    )

    inventory_rows = [
        (pid, random.randint(0, 60), random.randint(15, 25))
        for pid, _, _, _ in PRODUCTS
    ]
    cur.executemany(
        "INSERT INTO inventory VALUES (?, ?, ?)", inventory_rows
    )

    conn.commit()
    conn.close()
    print(f"business.db creada con {len(sales_rows)} ventas.")


if __name__ == "__main__":
    build()
