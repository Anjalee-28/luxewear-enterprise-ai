import sqlite3, hashlib
import pandas as pd

def hash_pwd(p: str) -> str:
    return hashlib.sha256(p.encode()).hexdigest()

conn = sqlite3.connect("luxewear_enterprise.db")
cur = conn.cursor()
cur.execute("PRAGMA foreign_keys = ON;")

# Drop existing tables for clean ETL migration
for t in ["order_items", "orders", "product_variants", "products", "customers", "store_faqs", "conversations", "support_tickets", "admin_users"]:
    cur.execute(f"DROP TABLE IF EXISTS {t}")

# Create 9 Normalized Tables
cur.executescript("""
CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    material TEXT,
    base_price REAL NOT NULL
);

CREATE TABLE product_variants (
    variant_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL,
    sku TEXT UNIQUE NOT NULL,
    size TEXT NOT NULL,
    color TEXT NOT NULL,
    stock_qty INTEGER NOT NULL CHECK(stock_qty >= 0),
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
);

CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    email TEXT,
    phone_number TEXT NOT NULL,
    delivery_address TEXT
);

CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    total_amount REAL NOT NULL,
    payment_method TEXT NOT NULL,
    payment_status TEXT NOT NULL,
    order_status TEXT NOT NULL,
    tracking_number TEXT,
    order_date TEXT,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    variant_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price REAL NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
    FOREIGN KEY (variant_id) REFERENCES product_variants(variant_id)
);

CREATE TABLE store_faqs (
    faq_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL
);

CREATE TABLE conversations (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    sender_type TEXT NOT NULL,
    message_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE support_tickets (
    ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    customer_name TEXT,
    contact_info TEXT,
    issue_summary TEXT NOT NULL,
    status TEXT DEFAULT 'Open',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE admin_users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL
);
""")

# 1. Migrate Products & Generate Normalized Size/Color Variants
df_prod = pd.read_excel("Products.xlsx")
for _, r in df_prod.iterrows():
    pid = str(r["Product_ID"]).strip()
    cur.execute("INSERT INTO products VALUES (?, ?, ?, ?, ?)",
                (pid, r["Product_Name"], r["Category"], r["Material"], float(r["Price_USD"])))
    
    sizes = [s.strip() for s in str(r["Available_Sizes"]).split(";") if s.strip()]
    colors = [c.strip() for c in str(r["Available_Colours"]).split(";") if c.strip()]
    total_stock = int(r["Stock_Quantity"])
    combos = [(s, c) for s in sizes for c in colors]
    per_var_stock = max(1, total_stock // len(combos)) if combos else 0
    
    for s, c in combos:
        sku = f"{pid}-{c[:3].upper()}-{s.upper()}"
        # Make P001 Black M out-of-stock (0) to demonstrate smart alternative recommendations!
        qty = 0 if (pid == "P001" and c.lower() == "black" and s.upper() == "M") else per_var_stock
        cur.execute("INSERT INTO product_variants (product_id, sku, size, color, stock_qty) VALUES (?, ?, ?, ?, ?)",
                    (pid, sku, s, c, qty))

# 2. Migrate Customers
df_cust = pd.read_excel("Customers.xlsx")
for _, r in df_cust.iterrows():
    cur.execute("INSERT INTO customers VALUES (?, ?, ?, ?, ?)",
                (str(r["Customer_ID"]).strip(), r["Customer_Name"], r["Email"], str(r["Phone"]), r["Shipping_Address"]))

# 3. Migrate Orders & Normalized Order Items
df_ord = pd.read_excel("Orders.xlsx")
prod_prices = dict(zip(df_prod["Product_ID"].astype(str).str.strip(), df_prod["Price_USD"].astype(float)))
for _, r in df_ord.iterrows():
    oid = str(r["Order_ID"]).strip()
    cid = str(r["Customer_ID"]).strip()
    pid = str(r["Product_ID"]).strip()
    qty = int(r["Quantity"])
    unit_p = prod_prices.get(pid, 0.0)
    trk = None if pd.isna(r["Tracking_Number"]) else str(r["Tracking_Number"]).strip()
    pay_stat = str(r["Payment_Status"]).strip()
    pay_method = "Card" if pay_stat == "Paid" else "Pending_Selection"
    
    cur.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (oid, cid, round(unit_p * qty, 2), pay_method, pay_stat, str(r["Delivery_Status"]).strip(), trk, str(r["Order_Date"])[:10]))
    
    var_row = cur.execute("SELECT variant_id FROM product_variants WHERE product_id = ? LIMIT 1", (pid,)).fetchone()
    if var_row:
        cur.execute("INSERT INTO order_items (order_id, variant_id, quantity, unit_price) VALUES (?, ?, ?, ?)",
                    (oid, var_row[0], qty, unit_p))

# 4. Migrate FAQs
df_faq = pd.read_excel("FAQs.xlsx")
for _, r in df_faq.iterrows():
    cur.execute("INSERT INTO store_faqs VALUES (?, ?, ?)",
                (str(r["FAQ_ID"]).strip(), r["Question"], r["Answer"]))

# 5. Seed Sample Ticket & Role-Based Users
cur.execute("INSERT INTO support_tickets (session_id, customer_name, contact_info, issue_summary, status) VALUES (?, ?, ?, ?, ?)",
            ("SES-100", "Sophia Brown", "sophia.brown@example.com", "Customer asked about international shipping rates for bulk order.", "Open"))

cur.executemany("INSERT INTO admin_users (username, password_hash, role) VALUES (?, ?, ?)", [
    ("admin", hash_pwd("admin123"), "Admin"),
    ("staff", hash_pwd("staff123"), "Support_Agent")
])

conn.commit()
conn.close()
print("SUCCESS: All 4 Excel sheets migrated into normalized 'luxewear_enterprise.db'!")