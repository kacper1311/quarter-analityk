# QUARTER Analytics — Etap 1

Aplikacja Streamlit która łączy się z Allegro API i wyświetla pobrane zamówienia.

---

## Struktura projektu

```
quarter_analytics/
├── .env                  # Client ID i Secret — NIE commituj
├── .env.example          # Szablon
├── .gitignore
├── requirements.txt
├── auth.py               # Autoryzacja Allegro (Device Flow)
├── allegro.py            # Pobieranie danych z API
└── app.py                # Aplikacja Streamlit
```

---

## `.gitignore`
```
.env
tokens.json
__pycache__/
*.pyc
.DS_Store
```

---

## `.env.example`
```
ALLEGRO_CLIENT_ID=tu_wklej_client_id
ALLEGRO_CLIENT_SECRET=tu_wklej_client_secret
```

---

## `requirements.txt`
```
requests>=2.31.0
python-dotenv>=1.0.0
streamlit>=1.32.0
pandas>=2.0.0
```

---

## `auth.py`

Obsługuje autoryzację Device Flow i zapis tokenów do `tokens.json`.

**`authorize()`**
- POST `https://allegro.pl/auth/oauth/device` (Basic Auth: client_id:client_secret, body: `client_id=...`)
- Zwraca dict z `verification_uri_complete`, `device_code`, `interval`, `expires_in`
- NIE wyświetla nic — link zwraca do Streamlit

**`poll_for_token(device_code, interval)`**
- Polling co `interval` sekund
- POST `https://allegro.pl/auth/oauth/token` z `grant_type=urn:ietf:params:oauth:grant-type:device_code&device_code=...`
- Przy `error=authorization_pending` — zwraca None
- Przy sukcesie — zapisuje tokeny do `tokens.json` (dodaj `created_at = time.time()`), zwraca token dict

**`get_token()`**
- Wczytuje `tokens.json`
- Jeśli brak pliku → zwraca None
- Jeśli token wygasł (`created_at + expires_in < now`) → wywołuje `refresh()`
- Zwraca `access_token` jako string lub None

**`refresh()`**
- POST `https://allegro.pl/auth/oauth/token` Basic Auth
- Body: `grant_type=refresh_token&refresh_token={token}`
- Zapisuje nowe tokeny, zwraca access_token string
- Przy błędzie → zwraca None (Streamlit pokaże przycisk do ponownej autoryzacji)

---

## `allegro.py`

**`get_headers(token)`**
```python
return {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.allegro.public.v1+json"
}
```

**`get_orders(token, date_from=None)`**
- GET `https://api.allegro.pl/order/checkout-forms`
- Parametry: `status=BOUGHT`, `limit=100`
- Opcjonalnie `lineItems.boughtAt.gte=date_from` (format: `2026-03-01T00:00:00Z`)
- Paginacja: offset += 100 dopóki len(wyniki) == 100
- Zwraca listę zamówień (raw JSON)

**`parse_orders(orders_raw)`**
- Przetwarza raw JSON na listę słowników gotowych do DataFrame
- Każdy wiersz:
```python
{
    "order_id": str,
    "date": str,          # YYYY-MM-DD z pola boughtAt
    "offer_name": str,    # lineItems[0].offer.name
    "quantity": int,      # lineItems[0].quantity
    "sale_price": float,  # payment.paidAmount.amount
    "delivery_cost": float, # delivery.cost.amount
    "status": str
}
```
- Jeśli zamówienie ma wiele lineItems — osobny wiersz dla każdego

---

## `app.py`

Aplikacja Streamlit z prostym flow:

### Stan autoryzacji (session_state)

```python
if "token" not in st.session_state:
    st.session_state.token = get_token()  # próba wczytania z tokens.json
if "orders" not in st.session_state:
    st.session_state.orders = None
if "auth_flow" not in st.session_state:
    st.session_state.auth_flow = None    # dict z device_code itp.
```

### Ekran 1 — Brak autoryzacji

Jeśli `st.session_state.token` jest None:

```
QUARTER Analytics

Połącz konto Allegro aby rozpocząć.

[Połącz z Allegro]  ← przycisk
```

Po kliknięciu przycisku:
- Wywołuje `authorize()`, zapisuje wynik do `st.session_state.auth_flow`
- Wyświetla link klikalny: `Kliknij tutaj aby autoryzować`
- Wyświetla przycisk `[Już autoryzowałem]`

Po kliknięciu `[Już autoryzowałem]`:
- Wywołuje `poll_for_token(device_code, interval)`
- Jeśli sukces → zapisuje token do `st.session_state.token`, `st.rerun()`
- Jeśli błąd → wyświetla `"Spróbuj ponownie — autoryzacja nie została potwierdzona"`

### Ekran 2 — Główny dashboard

Jeśli token istnieje:

**Sidebar:**
```
QUARTER Analytics
─────────────────
Zakres dat:
[ Data od ] [ Data do ]

[Pobierz zamówienia]

─────────────────
[Wyloguj]
```

**Główny obszar — przed pobraniem:**
```
Wybierz zakres dat i kliknij "Pobierz zamówienia"
```

**Główny obszar — po pobraniu:**

Metryki na górze (st.metric):
- Liczba zamówień
- Łączny przychód (zł)
- Średnia wartość zamówienia (zł)

Tabela poniżej:
- `st.dataframe` z parsed orders
- Kolumny: Data, Nazwa oferty, Ilość, Cena sprzedaży, Koszt dostawy, Status
- Sortowanie domyślnie po dacie malejąco

---

## Uruchomienie

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# uzupełnij .env swoimi danymi
streamlit run app.py
```

Aplikacja otworzy się w przeglądarce na `http://localhost:8501`