"""
pages/compare.py — страница сравнения авторов
"""
import dash
from dash import html, dcc, callback, Input, Output
import dash_bootstrap_components as dbc
import pandas as pd
from utils import logger
from etl import db_session, get_author_stats, get_author_name, init_db

dash.register_page(__name__, path='/compare', name='Сравнение авторов')

# ===== ЗАГРУЗКА СПИСКА АВТОРОВ =====
def load_authors_list():
    """Возвращает список словарей для options dropdown."""
    init_db()  # гарантируем, что таблицы существуют (иначе первый запуск упадёт)
    with db_session() as conn:
        df = pd.read_sql("SELECT id, name FROM authors ORDER BY name", conn)
    return [{'label': row['name'], 'value': row['id']} for _, row in df.iterrows()]

# ===== LAYOUT =====
def layout(**kwargs):
    """Строится при каждом открытии страницы.

    Список авторов читается из БД в момент перехода, а не при импорте модуля:
    на свежем компьютере при импорте база ещё пустая (авто-ETL заполняет её
    чуть позже, перед стартом сервера), и список, посчитанный один раз при
    импорте, остался бы пустым навсегда.
    """
    options = load_authors_list()  # реальные авторы
    return html.Div([
        html.H1("📊 Сравнение авторов", className="mt-4"),
        html.Hr(),
        dbc.Row([
            dbc.Col([
                html.Label("Выберите первого автора:"),
                dcc.Dropdown(
                    id='author1-dropdown',
                    options=options,
                    placeholder='Выберите автора...'
                )
            ], width=6),
            dbc.Col([
                html.Label("Выберите второго автора:"),
                dcc.Dropdown(
                    id='author2-dropdown',
                    options=options,
                    placeholder='Выберите автора...'
                )
            ], width=6),
        ], className="mb-4"),
        html.Div(id='comparison-results', children=[
            html.P("Выберите двух авторов для сравнения", className="text-muted")
        ])
    ])

# ===== CALLBACK СРАВНЕНИЯ =====
@callback(
    Output('comparison-results', 'children'),
    Input('author1-dropdown', 'value'),
    Input('author2-dropdown', 'value')
)
def update_comparison(author1_id, author2_id):
    if not author1_id or not author2_id:
        return html.P("Пожалуйста, выберите двух авторов", className="text-warning")
    if author1_id == author2_id:
        return html.P("Выберите разных авторов", className="text-danger")

    # Получаем данные об авторах
    try:
        stats1 = get_author_stats(author1_id)
        stats2 = get_author_stats(author2_id)
        name1 = get_author_name(author1_id)
        name2 = get_author_name(author2_id)
    except Exception as e:
        logger.error(f"Ошибка получения данных: {e}")
        return html.P("Ошибка загрузки данных", className="text-danger")

    # Формируем таблицу сравнения
    table = dbc.Table([
        html.Thead(html.Tr([
            html.Th("Метрика"),
            html.Th(name1),
            html.Th(name2)
        ])),
        html.Tbody([
            html.Tr([html.Td("Публикации"), html.Td(stats1[0]), html.Td(stats2[0])]),
            html.Tr([html.Td("Цитирования"), html.Td(stats1[1]), html.Td(stats2[1])]),
            html.Tr([html.Td("Соавторы"), html.Td(stats1[2]), html.Td(stats2[2])]),
            # Можно добавить h-index, если есть функция
        ])
    ], bordered=True, hover=True, className="mt-3")

    return table