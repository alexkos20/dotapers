"""
pages/analytics.py — страница общей аналитики
"""
import dash
from dash import html, dcc, callback, Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import pandas as pd
from utils import logger, timeit
from etl import get_connection

dash.register_page(__name__, path='/analytics', name='Аналитика')

layout = html.Div([
    html.H1("📈 Общая аналитика", className="mt-4"),
    html.Hr(),
    dbc.Row([
        dbc.Col([
            html.Div([
                html.H6("🏷️ Топ-10 тем (все публикации)"),
                dcc.Graph(id='global-topics-chart', config={'displayModeBar': False})
            ], className="p-3", style={'backgroundColor': 'white', 'borderRadius': '8px', 'border': '1px solid #e0e0e0'})
        ], width=6),
        dbc.Col([
            html.Div([
                html.H6("📈 Динамика публикаций по годам (все)"),
                dcc.Graph(id='global-trend-chart', config={'displayModeBar': False})
            ], className="p-3", style={'backgroundColor': 'white', 'borderRadius': '8px', 'border': '1px solid #e0e0e0'})
        ], width=6),
    ]),
    dbc.Row([
        dbc.Col([
            html.Div([
                html.H6("👥 Топ-10 авторов по публикациям"),
                dcc.Graph(id='top-authors-chart', config={'displayModeBar': False})
            ], className="p-3", style={'backgroundColor': 'white', 'borderRadius': '8px', 'border': '1px solid #e0e0e0'})
        ], width=12),
    ], className="mt-4")
])

@callback(
    Output('global-topics-chart', 'figure'),
    Input('global-topics-chart', 'id')  # триггер при загрузке
)
@timeit
def load_global_topics(_):
    with get_connection() as conn:
        df_pubs = pd.read_sql("SELECT topics FROM publications", conn)
    topics = []
    for topics_str in df_pubs['topics'].dropna():
        if topics_str and isinstance(topics_str, str):
            # Темы хранятся по одной на публикацию; имя темы может содержать
            # запятую ('Language, Discourse, Communication Strategies') — режем
            # только по ';' (в именах тем не встречается), иначе одна тема
            # раскалывается на фейковые части.
            for t in topics_str.split(';'):
                cleaned = t.strip()
                if cleaned:
                    topics.append(cleaned)
    from collections import Counter
    topic_counts = Counter(topics)
    top_topics = topic_counts.most_common(10)
    if not top_topics:
        fig = go.Figure()
        fig.update_layout(title="Нет данных", height=300)
        return fig
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[count for _, count in top_topics[::-1]],
        y=[topic[:50] for topic, _ in top_topics[::-1]],
        orientation='h',
        marker_color='#2ECC71',
        text=[count for _, count in top_topics[::-1]],
        textposition='outside'
    ))
    fig.update_layout(
        title=None,
        xaxis_title="Publications",
        yaxis_title="",
        height=350,
        margin=dict(l=160, r=20, t=20, b=20),
        plot_bgcolor='white'
    )
    return fig

@callback(
    Output('global-trend-chart', 'figure'),
    Input('global-trend-chart', 'id')
)
@timeit
def load_global_trend(_):
    with get_connection() as conn:
        df_pubs = pd.read_sql("SELECT publication_year FROM publications", conn)
    yearly = df_pubs['publication_year'].dropna()
    if yearly.empty:
        fig = go.Figure()
        fig.update_layout(title="Нет данных", height=300)
        return fig
    yearly = yearly.astype(int)
    counts = yearly.value_counts().sort_index()
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=counts.index,
        y=counts.values,
        marker_color='#3498db',
        text=counts.values,
        textposition='auto'
    ))
    fig.update_layout(
        title=None,
        xaxis_title="Year",
        yaxis_title="Papers",
        height=350,
        margin=dict(l=40, r=20, t=20, b=40),
        plot_bgcolor='white'
    )
    fig.update_xaxes(showgrid=True, gridcolor='#e0e0e0')
    fig.update_yaxes(showgrid=True, gridcolor='#e0e0e0')
    return fig

@callback(
    Output('top-authors-chart', 'figure'),
    Input('top-authors-chart', 'id')
)
@timeit
def load_top_authors(_):
    with get_connection() as conn:
        query = """
            SELECT a.name, COUNT(au.publication_id) as pub_count
            FROM authors a
            JOIN authorship au ON a.id = au.author_id
            GROUP BY a.id
            ORDER BY pub_count DESC
            LIMIT 10
        """
        df = pd.read_sql(query, conn)
    if df.empty:
        fig = go.Figure()
        fig.update_layout(title="Нет данных", height=300)
        return fig
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df['pub_count'][::-1],
        y=df['name'][::-1],
        orientation='h',
        marker_color='#E67E22',
        text=df['pub_count'][::-1],
        textposition='outside'
    ))
    fig.update_layout(
        title=None,
        xaxis_title="Publications",
        yaxis_title="",
        height=350,
        margin=dict(l=200, r=20, t=20, b=20),
        plot_bgcolor='white'
    )
    return fig