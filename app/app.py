import os

import dash
from dash import html, dcc, page_container
import dash_bootstrap_components as dbc

app = dash.Dash(__name__, use_pages=True, external_stylesheets=[dbc.themes.FLATLY])
server = app.server


# ===== НАВИГАЦИОННАЯ ПАНЕЛЬ =====
navbar = dbc.NavbarSimple(
    children=[
        dbc.NavItem(dbc.NavLink(page['name'], href=page['relative_path']))
        for page in dash.page_registry.values()
        if page.get('path_template') is None  # страницы с параметрами (профиль автора) не имеют фиксированного пути
    ],
    brand="SPBU Dashboard",
    brand_href="/",
    color="primary",
    dark=True,
    className="mb-4"
)

# ===== ОСНОВНОЙ LAYOUT =====
app.layout = html.Div([
    dcc.Location(id='url'),
    navbar,
    dcc.Store(id='global-store', storage_type='memory'),
    dcc.Store(id='graph-cache', storage_type='session'),
    page_container
])

# ===== ЗАПУСК =====
if __name__ == '__main__':
    # debug включается через DASH_DEBUG=1; по умолчанию выключен,
    # чтобы не было reloader-процессов, удерживающих порт.
    # host=0.0.0.0 — чтобы порт был доступен снаружи (в т.ч. из Docker).
    debug = os.environ.get('DASH_DEBUG', '0').lower() in ('1', 'true', 'yes')
    print("Зарегистрированные страницы:", dash.page_registry.keys())
    app.run(host='0.0.0.0', port=8050, debug=debug)

