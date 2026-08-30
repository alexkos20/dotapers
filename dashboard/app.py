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
    print("Зарегистрированные страницы:", dash.page_registry.keys())
    app.run(debug=True, port=8050)

