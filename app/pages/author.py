import dash
from dash import html
import dash_bootstrap_components as dbc
from utils import logger
from etl import (
    get_author_stats, get_author_publications,
    get_author_coauthors, get_author_topics, get_author_name
)

dash.register_page(__name__, path_template='/author/<author_id>', name='Профиль автора')

def layout(author_id=None, **kwargs):
    """author_id приходит из URL (path_template) — раскодированный целиком."""
    if not author_id or author_id == 'none':
        return html.Div("Автор не указан", className="text-warning mt-4")

    # Загружаем данные
    try:
        pub_count, citations, coauthors_count = get_author_stats(author_id)
        publications = get_author_publications(author_id)
        coauthors = get_author_coauthors(author_id)
        topics = get_author_topics(author_id)
    except Exception as e:
        logger.error(f"Ошибка загрузки профиля {author_id}: {e}")
        return html.Div("Ошибка загрузки данных", className="text-danger mt-4")

    # Карточка с метриками
    author_name = get_author_name(author_id)
    card = dbc.Card([
        dbc.CardBody([
            html.H4([
                f"👤 {author_name}",
                html.A("🔗", href=author_id, target="_blank",
                       title="Открыть профиль в OpenAlex",
                       className="text-decoration-none ms-2"),
            ], className="card-title"),
            html.Hr(),
            dbc.Row([
                dbc.Col(html.Div([
                    html.Div("📄", style={'fontSize': '20px'}),
                    html.Div(str(pub_count), style={'fontSize': '24px', 'fontWeight': 'bold'}),
                    html.Div("Публикаций", className="small text-muted")
                ], className="text-center"), width=3),
                dbc.Col(html.Div([
                    html.Div("📊", style={'fontSize': '20px'}),
                    html.Div(f"{citations:,}", style={'fontSize': '24px', 'fontWeight': 'bold'}),
                    html.Div("Цитирований", className="small text-muted")
                ], className="text-center"), width=3),
                dbc.Col(html.Div([
                    html.Div("👥", style={'fontSize': '20px'}),
                    html.Div(str(coauthors_count), style={'fontSize': '24px', 'fontWeight': 'bold'}),
                    html.Div("Соавторов", className="small text-muted")
                ], className="text-center"), width=3),
                dbc.Col(html.Div([
                    html.Div("📈", style={'fontSize': '20px'}),
                    html.Div("—", style={'fontSize': '24px', 'fontWeight': 'bold'}),
                    html.Div("H‑индекс", className="small text-muted")
                ], className="text-center"), width=3),
            ])
        ])
    ], className="mb-4")

    # Список публикаций
    pubs_list = []
    if publications:
        for pub in publications[:20]:  # покажем последние 20
            pubs_list.append(html.Li([
                html.A(pub['title'], href=pub['id'], target="_blank") if pub['id'].startswith('http') else html.Span(pub['title']),
                html.Span(f" ({pub.get('publication_year', 'н/д')})", className="text-muted"),
                html.Span(f" | Цит.: {pub.get('cited_by_count', 0)}", className="small text-muted")
            ]))
    else:
        pubs_list.append(html.Li("Публикаций не найдено"))

    # Список соавторов
    coauthors_list = []
    if coauthors:
        for ca in coauthors[:20]:
            coauthors_list.append(html.Li([
                html.A(ca['name'], href=f"/author/{ca['id']}"),
                html.Span(f" ({ca['joint_works']} совместных работ)", className="small text-muted")
            ]))
    else:
        coauthors_list.append(html.Li("Соавторов не найдено"))

    # Темы
    topics_list = []
    if topics:
        for topic, count in topics:
            topics_list.append(html.Li(f"{topic} ({count})"))
    else:
        topics_list.append(html.Li("Тем не найдено"))

    return html.Div([
        card,
        dbc.Row([
            dbc.Col([
                html.H5("📚 Публикации"),
                html.Ul(pubs_list, className="list-unstyled")
            ], width=7),
            dbc.Col([
                html.H5("🤝 Соавторы"),
                html.Ul(coauthors_list, className="list-unstyled"),
                html.Hr(),
                html.H5("🏷️ Основные темы"),
                html.Ul(topics_list, className="list-unstyled")
            ], width=5)
        ])
    ])
