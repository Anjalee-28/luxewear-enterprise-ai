import os, sqlite3, hashlib, uuid
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

st.set_page_config(page_title="LuxeWear | AI Enterprise Suite", page_icon="✨", layout="wide", initial_sidebar_state="collapsed")

def hash_pwd(p: str) -> str:
    return hashlib.sha256(p.encode()).hexdigest()

def log_chat(session_id: str, sender: str, text: str):
    conn = sqlite3.connect("luxewear_enterprise.db")
    conn.execute("INSERT INTO conversations (session_id, sender_type, message_text) VALUES (?, ?, ?)", (session_id, sender, text))
    conn.commit()
    conn.close()

def search_inventory(keyword: str = "", size: str = "", color: str = "") -> str:
    """Searches products and variant stock by keyword, size, and color. Automatically suggests alternatives if out of stock."""
    conn = sqlite3.connect("luxewear_enterprise.db")
    rows = conn.cursor().execute("SELECT p.product_id, p.name, p.category, p.material, p.base_price, v.sku, v.size, v.color, v.stock_qty FROM products p JOIN product_variants v ON p.product_id = v.product_id").fetchall()
    conn.close()
    if not rows: return "No products in catalog."

    kw, sz, cl = keyword.lower().strip(), size.upper().strip(), color.lower().strip()
    exact, alternatives = [], []
    for pid, name, cat, mat, price, sku, v_sz, v_cl, qty in rows:
        kw_m = (not kw) or any(w in f"{name} {cat} {mat}".lower() for w in kw.split())
        sz_m = (not sz) or (v_sz.upper() == sz)
        cl_m = (not cl) or (cl in v_cl.lower())
        info = f"- {name} [{sku}] | Size: {v_sz}, Color: {v_cl} | Price: ${price:.2f} | Stock: {qty}"
        if kw_m and sz_m and cl_m and qty > 0: exact.append(info)
        elif kw_m and qty > 0: alternatives.append(info)

    res = "EXACT IN-STOCK MATCHES:\n" + "\n".join(exact) if exact else f"EXACT MATCH UNAVAILABLE ({keyword} {color} {size})."
    if alternatives: res += "\n\nAVAILABLE IN-STOCK ALTERNATIVES:\n" + "\n".join(alternatives[:6])
    return res

def place_confirmed_order(customer_name: str, phone: str, email: str, address: str, sku: str, quantity: int, payment_method: str) -> str:
    """Creates a confirmed order in an atomic SQL transaction and deducts variant stock. Payment method must be 'COD' or 'Card'."""
    conn = sqlite3.connect("luxewear_enterprise.db")
    cur = conn.cursor()
    try:
        cur.execute("BEGIN TRANSACTION")
        var = cur.execute("SELECT v.variant_id, v.stock_qty, p.base_price, p.name FROM product_variants v JOIN products p ON v.product_id = p.product_id WHERE v.sku = ?", (sku.strip().upper(),)).fetchone()
        if not var:
            conn.rollback()
            return f"Error: SKU '{sku}' not found."
        vid, stock, price, pname = var
        if stock < quantity:
            conn.rollback()
            return f"Insufficient stock for {sku}. Only {stock} left."

        cust = cur.execute("SELECT customer_id FROM customers WHERE phone_number = ?", (phone.strip(),)).fetchone()
        if cust:
            cid = cust[0]
            cur.execute("UPDATE customers SET full_name=?, email=?, delivery_address=? WHERE customer_id=?", (customer_name, email, address, cid))
        else:
            cid = f"C{uuid.uuid4().hex[:5].upper()}"
            cur.execute("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", (cid, customer_name, email, phone.strip(), address))

        oid = f"O{uuid.uuid4().hex[:5].upper()}"
        total = round(price * quantity, 2)
        pm = "Card" if "card" in payment_method.lower() else "COD"
        p_status = "Pending_Gateway" if pm == "Card" else "Unpaid_COD"

        cur.execute("INSERT INTO orders (order_id, customer_id, total_amount, payment_method, payment_status, order_status) VALUES (?, ?, ?, ?, ?, ?)", (oid, cid, total, pm, p_status, "Confirmed"))
        cur.execute("INSERT INTO order_items (order_id, variant_id, quantity, unit_price) VALUES (?, ?, ?, ?)", (oid, vid, quantity, price))
        cur.execute("UPDATE product_variants SET stock_qty = stock_qty - ? WHERE variant_id = ?", (quantity, vid))
        conn.commit()
        pay_msg = f"Secure Stripe Checkout Link: https://stripe.com{oid} (Status: Pending_Gateway)" if pm == "Card" else "Payment: Cash on Delivery (Unpaid_COD)"
        return f"ORDER CONFIRMED! Order ID: {oid} | Item: {pname} ({sku}) x{quantity} | Total: ${total:.2f} | {pay_msg}"
    except Exception as e:
        conn.rollback()
        return f"Transaction failed: {e}"
    finally:
        conn.close()

def track_order_status(order_id: str) -> str:
    """Fetches real-time order details using Order ID."""
    conn = sqlite3.connect("luxewear_enterprise.db")
    row = conn.cursor().execute("SELECT o.order_id, c.full_name, o.total_amount, o.payment_status, o.order_status, o.tracking_number FROM orders o JOIN customers c ON o.customer_id = c.customer_id WHERE o.order_id = ?", (order_id.strip().upper(),)).fetchone()
    conn.close()
    return f"Order {row[0]} for {row[1]}: Total ${row[2]:.2f} | Payment: {row[3]} | Delivery: {row[4]} | Tracking: {row[5] or 'Pending Dispatch'}" if row else f"Order '{order_id}' not found."
def escalate_to_human(customer_name: str, contact_info: str, issue_summary: str) -> str:
    """Logs a human support ticket when a customer requests staff assistance or has a complex dispute."""
    conn = sqlite3.connect("luxewear_enterprise.db")
    sid = st.session_state.get("session_id", "WEB-SESSION")
    cur = conn.cursor()
    cur.execute("INSERT INTO support_tickets (session_id, customer_name, contact_info, issue_summary) VALUES (?, ?, ?, ?)", (sid, customer_name, contact_info, issue_summary))
    tid = cur.lastrowid
    conn.commit()
    conn.close()
    return f"Support Ticket #{tid} created. A specialist will contact {customer_name} at {contact_info} shortly."

st.markdown("""
<style>
    @import url('https://googleapis.com');
    html, body, [class*="css"] { font-family: 'Plus Jakarta Sans', sans-serif; font-size: 14px; color: #f1f5f9; }
    #MainMenu, footer { visibility: hidden; }
    header[data-testid="stHeader"] { background: rgba(11, 15, 35, 0.85) !important; backdrop-filter: blur(10px); }
    .stApp { background: radial-gradient(circle at 15% 10%, #1e1b4b 0%, #0f172a 45%, #090d16 100%) !important; }
    section[data-testid="stSidebar"] { background: linear-gradient(180deg, #13112b 0%, #0b0f1f 100%) !important; border-right: 1px solid rgba(139, 92, 246, 0.2); }
    .banner { background: linear-gradient(135deg, rgba(59, 130, 246, 0.18), rgba(139, 92, 246, 0.22)); backdrop-filter: blur(16px); border: 1px solid rgba(139, 92, 246, 0.35); border-radius: 16px; padding: 18px 24px; margin-bottom: 18px; display: flex; justify-content: space-between; align-items: center; }
    .banner-title { margin: 0; font-size: 1.25rem; font-weight: 700; background: linear-gradient(90deg, #93c5fd, #c4b5fd, #f0abfc); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .stButton > button { background: linear-gradient(135deg, rgba(59, 130, 246, 0.15), rgba(139, 92, 246, 0.18)) !important; color: #e0e7ff !important; border: 1px solid rgba(139, 92, 246, 0.35) !important; border-radius: 12px !important; font-weight: 500 !important; }
    .stButton > button:hover { background: linear-gradient(135deg, #3b82f6, #8b5cf6) !important; color: #ffffff !important; border-color: #a78bfa !important; }
    [data-testid="stChatMessage"] { background: rgba(30, 27, 75, 0.45) !important; border: 1px solid rgba(129, 140, 248, 0.2) !important; border-radius: 16px !important; padding: 14px 18px !important; }
    [data-testid="stBottom"], [data-testid="stBottom"] > div { background: rgba(11, 15, 35, 0.95) !important; border-top: 1px solid rgba(139, 92, 246, 0.25) !important; }
    [data-testid="stChatInput"] { background: rgba(30, 27, 75, 0.75) !important; border: 1px solid rgba(139, 92, 246, 0.45) !important; border-radius: 14px !important; }
    [data-testid="stChatInput"] textarea { color: #f8fafc !important; }
    [data-testid="stChatMessage"] p,
    [data-testid="stChatMessage"] li,
    [data-testid="stChatMessage"] span,
    [data-testid="stChatMessage"] strong {
        color: #ffffff !important;
    }
    [data-testid="stChatInput"] textarea {
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
    }
</style>
""", unsafe_allow_html=True)
with st.sidebar:
    st.markdown("### ✨ LuxeWear Suite")
    portal = st.radio("Select Workspace", ["🛍️ Customer AI Concierge", "🔐 Staff & Admin Portal"])
    st.divider()
    if portal == "🔐 Staff & Admin Portal":
        if "user_role" not in st.session_state: st.session_state.user_role = None
        if not st.session_state.user_role:
            st.markdown("#### Staff Login")
            u = st.text_input("Username", placeholder="Enter username")
            p = st.text_input("Password", type="password", placeholder="Enter password")
            if st.button("Sign In", use_container_width=True):
                conn = sqlite3.connect("luxewear_enterprise.db")
                usr = conn.cursor().execute("SELECT role FROM admin_users WHERE username=? AND password_hash=?", (u.strip(), hash_pwd(p))).fetchone()
                conn.close()
                if usr:
                    st.session_state.user_role, st.session_state.username = usr, u.strip()
                    st.rerun()
                else: st.error("Invalid credentials.")
        else:
            st.success(f"Logged in: **{st.session_state.username}** ({st.session_state.user_role})")
            if st.button("Logout", use_container_width=True):
                st.session_state.user_role = None
                st.rerun()

if portal == "🛍️ Customer AI Concierge":
    if "session_id" not in st.session_state: st.session_state.session_id = f"SES-{uuid.uuid4().hex[:6].upper()}"
    if "cust_msgs" not in st.session_state:
        st.session_state.cust_msgs = [{"role": "assistant", "content": "Welcome to **LuxeWear**! ✨ How may I assist you today? You can explore our collection, check size & color availability, track an existing order, or place a new order."}]

    st.markdown('<div class="banner"><div><div class="banner-title">✨ LuxeWear AI Concierge</div><p style="margin:2px 0 0 0;color:#cbd5e1;font-size:0.8rem;">24/7 Personal Stylist, Live Inventory & Order Support</p></div><span style="background:rgba(16,185,129,0.15);border:1px solid rgba(16,185,129,0.4);color:#34d399;padding:4px 12px;border-radius:99px;font-weight:600;font-size:0.75rem;">● Online</span></div>', unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    qp = None
    if c1.button("🛍️ Browse Collection", use_container_width=True): qp = "Show me your current collection, available sizes, colors, and prices."
    if c2.button("📦 Track My Order", use_container_width=True): qp = "I would like to track my order."
    if c3.button("🔄 Returns & Refunds", use_container_width=True): qp = "What is your return and refund policy?"

    for m in st.session_state.cust_msgs:
        with st.chat_message(m["role"], avatar="✨" if m["role"] == "assistant" else "👤"): st.markdown(m["content"])

    inp = qp or st.chat_input("Ask about styles, availability, or enter your Order ID...")
    if inp:
        st.session_state.cust_msgs.append({"role": "user", "content": inp})
        log_chat(st.session_state.session_id, "Customer", inp)
        with st.chat_message("user", avatar="👤"): st.markdown(inp)

        conn = sqlite3.connect("luxewear_enterprise.db")
        faqs = "\n".join([f"Q: {r} | A: {r}" for r in conn.cursor().execute("SELECT question, answer FROM store_faqs").fetchall()])
        conn.close()

        hist = "\n".join([f"{m['role']}: {m['content']}" for m in st.session_state.cust_msgs[-8:]])
        sys_prompt = f"You are LuxeWear's AI Concierge. Understand typos/informal English naturally. ALWAYS use `search_inventory` to check variant stock and recommend in-stock alternatives if unavailable. If customer wants to track an order without giving an Order ID, ask for their Order ID. Before `place_confirmed_order`, collect Full Name, Phone, Email, Address, SKU, Quantity, and Payment (COD/Card) and ask confirmation. NEVER collect card numbers in chat. Refunds happen after return inspection. Use `escalate_to_human` for complex issues. FAQs:\n{faqs}\nHistory:\n{hist}"

        with st.chat_message("assistant", avatar="✨"):
            with st.spinner("LuxeWear AI is typing..."):
                reply, err = None, ""
                for mod in ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash-lite"]:
                    try:
                        res = client.models.generate_content(model=mod, contents=f"{sys_prompt}\nCustomer: {inp}", config=types.GenerateContentConfig(tools=[search_inventory, place_confirmed_order, track_order_status, escalate_to_human], temperature=0.3))
                        reply = res.text
                        break
                    except Exception as e: err = str(e)
                if reply:
                    st.markdown(reply)
                    st.session_state.cust_msgs.append({"role": "assistant", "content": reply})
                    log_chat(st.session_state.session_id, "AI_Agent", reply)
                else: st.error(f"Busy: {err}")

else:
    if not st.session_state.get("user_role"):
        st.info("👈 Open the sidebar (top-left arrow `>`) to sign in to the Staff & Admin Portal.")
        st.stop()

    role = st.session_state.user_role
    st.markdown(f'<div class="banner"><div><div class="banner-title">📊 LuxeWear Management Portal</div><p style="margin:2px 0 0 0;color:#cbd5e1;font-size:0.8rem;">Role-Based Access Control: <b>{role}</b></p></div></div>', unsafe_allow_html=True)
    conn = sqlite3.connect("luxewear_enterprise.db")

    if role == "Admin":
        tabs = st.tabs(["📈 Analytics", "👗 Inventory", "📦 Orders & CRM", "🎫 Tickets", "💬 Chat Logs"])
        with tabs:
            rev = conn.cursor().execute("SELECT COALESCE(SUM(total_amount),0) FROM orders WHERE payment_status='Paid'").fetchone()
            tot_ord = conn.cursor().execute("SELECT COUNT(*) FROM orders").fetchone()
            open_tkt = conn.cursor().execute("SELECT COUNT(*) FROM support_tickets WHERE status='Open'").fetchone()
            low_stk = conn.cursor().execute("SELECT COUNT(*) FROM product_variants WHERE stock_qty <= 2").fetchone()
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Paid Revenue", f"${rev:,.2f}")
            k2.metric("Total Orders", tot_ord)
            k3.metric("Open Tickets", open_tkt)
            k4.metric("Low/Out Stock", low_stk)
            df_sales = pd.read_sql_query("SELECT order_status, SUM(total_amount) as revenue FROM orders GROUP BY order_status", conn)
            if not df_sales.empty: st.bar_chart(df_sales.set_index("order_status")["revenue"])

        with tabs:
            st.dataframe(pd.read_sql_query("SELECT v.variant_id, p.name, v.sku, v.size, v.color, p.base_price, v.stock_qty FROM product_variants v JOIN products p ON v.product_id=p.product_id", conn), hide_index=True, use_container_width=True)
            with st.expander("➕ Add New Product & Variant"):
                with st.form("add_prod"):
                    c1, c2 = st.columns(2)
                    p_id, p_name = c1.text_input("Product ID"), c2.text_input("Product Name")
                    p_cat, p_mat = c1.text_input("Category", "Dresses"), c2.text_input("Material", "Silk")
                    p_price, v_sku = c1.number_input("Price ($)", 1.0, 1000.0, 59.0), c2.text_input("SKU")
                    v_sz, v_cl = c1.text_input("Size", "M"), c2.text_input("Color", "Black")
                    v_qty = st.number_input("Stock Qty", 0, 500, 10)
                    if st.form_submit_button("Save Product"):
                        try:
                            conn.execute("INSERT OR IGNORE INTO products VALUES (?, ?, ?, ?, ?)", (p_id, p_name, p_cat, p_mat, p_price))
                            conn.execute("INSERT INTO product_variants (product_id, sku, size, color, stock_qty) VALUES (?, ?, ?, ?, ?)", (p_id, v_sku, v_sz, v_cl, v_qty))
                            conn.commit()
                            st.success("Saved!")
                            st.rerun()
                        except Exception as e: st.error(f"Error: {e}")

        with tabs:
            st.dataframe(pd.read_sql_query("SELECT o.order_id, c.full_name, o.total_amount, o.payment_method, o.payment_status, o.order_status FROM orders o JOIN customers c ON o.customer_id=c.customer_id", conn), hide_index=True, use_container_width=True)
            st.dataframe(pd.read_sql_query("SELECT oi.order_id, p.name, v.sku, v.size, v.color, oi.quantity, oi.unit_price FROM order_items oi JOIN product_variants v ON oi.variant_id=v.variant_id JOIN products p ON v.product_id=p.product_id", conn), hide_index=True, use_container_width=True)
        with tabs: st.dataframe(pd.read_sql_query("SELECT * FROM support_tickets ORDER BY ticket_id DESC", conn), hide_index=True, use_container_width=True)
        with tabs: st.dataframe(pd.read_sql_query("SELECT * FROM conversations ORDER BY message_id DESC LIMIT 50", conn), hide_index=True, use_container_width=True)
    else:
        st.warning("🔒 Restricted Staff View")
        t1, t2 = st.tabs(["🎫 Tickets", "💬 Chat Logs"])
        with t1: st.dataframe(pd.read_sql_query("SELECT * FROM support_tickets ORDER BY ticket_id DESC", conn), hide_index=True, use_container_width=True)
        with t2: st.dataframe(pd.read_sql_query("SELECT * FROM conversations ORDER BY message_id DESC LIMIT 50", conn), hide_index=True, use_container_width=True)
    conn.close()