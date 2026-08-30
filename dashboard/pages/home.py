import hashlib
import math
import pickle
from pathlib import Path

import dash
from dash import html, dcc, Input, Output, callback
import dash_cytoscape as cyto
import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
import networkx as nx
from collections import Counter
from functools import lru_cache
from utils import MAIN_AUTHORS, DB_PATH, logger, timeit
from etl import get_connection

# Регистрируем страницу
dash.register_page(__name__, path='/', name='Сеть соавторства')

# ===== СТИЛИ И ПАЛИТРА =====
COLOR_PALETTE = [
    '#FF3333', '#33CC33', '#3399FF', '#FF9933', '#9933CC',
    '#FF33CC', '#33CCCC', '#CC3333', '#33CC99', '#CC9933',
    '#993366', '#339966', '#CC3366', '#33CC66', '#FF6633',
    '#6633CC', '#FF33FF', '#33FF33', '#FFCC33', '#33FFCC'
]

STYLESHEET = [
    {
        'selector': 'node',
        'style': {
            'width': 'data(node_size)',
            'height': 'data(node_size)',
            'background-color': 'data(color)',
            'label': 'data(label)',
            'font-size': '10px',
            'font-weight': 'bold',
            'text-valign': 'center',
            'text-halign': 'center',
            'color': '#FFFFFF',
            'text-outline-width': 1,
            'text-outline-color': '#333333',
            'border-width': 2,
            'border-color': '#FFFFFF'
        }
    },
    {
        'selector': 'edge',
        'style': {
            'width': 1.0,
            'line-color': '#9a9b9c',
            'curve-style': 'bezier',
            'opacity': 0.6
        }
    }
]

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
@lru_cache(maxsize=1)
@timeit
def load_data_from_db():
    with get_connection() as conn:
        df_pubs = pd.read_sql("SELECT * FROM publications", conn)
        df_auth = pd.read_sql("SELECT * FROM authorship", conn)
    logger.info(f"Загружено {len(df_pubs)} публикаций из БД")
    return df_pubs, df_auth

# ===== ДИСКОВЫЙ КЭШ ГРАФА =====
# Хранит полный граф в pickle-файле, чтобы не перестраивать его при каждом
# открытии главной (и после рестарта контейнера). Ключ = состояние БД,
# поэтому после ETL кэш автоматически пересоздаётся.
GRAPH_CACHE_FILE = Path(__file__).resolve().parent.parent / 'graph_cache.pkl'

# Версия структуры закэшированного графа. Увеличивать при изменении
# графа/раскладки в коде — старый graph_cache.pkl станет невалидным.
CACHE_VERSION = 5

def _db_state_key():
    """Хэш состояния БД + версии кода — меняется после ETL или правок построителя."""
    with get_connection() as conn:
        pubs = pd.read_sql("SELECT COUNT(*) FROM publications", conn).iloc[0, 0]
        authors = pd.read_sql("SELECT COUNT(*) FROM authors", conn).iloc[0, 0]
        meta = pd.read_sql("SELECT value FROM etl_metadata WHERE key = 'last_etl_run'", conn)
        last = meta.iloc[0, 0] if not meta.empty else ''
    return hashlib.md5(f"{CACHE_VERSION}:{pubs}:{authors}:{last}".encode()).hexdigest()

def _load_graph_from_disk(key):
    if not GRAPH_CACHE_FILE.exists():
        return None
    try:
        with open(GRAPH_CACHE_FILE, 'rb') as f:
            saved_key, G = pickle.load(f)
        if saved_key == key:
            logger.info("Граф загружен из дискового кэша")
            return G
    except Exception as e:
        logger.warning(f"Кэш графа повреждён, будет пересоздан: {e}")
    return None

def _save_graph_to_disk(key, G):
    try:
        with open(GRAPH_CACHE_FILE, 'wb') as f:
            pickle.dump((key, G), f)
        logger.info("Граф сохранён в дисковый кэш")
    except Exception as e:
        logger.warning(f"Не удалось сохранить кэш графа: {e}")

def _build_full_graph():
    """Полное построение графа без фильтров."""
    df_pubs, df_auth = load_data_from_db()

    if df_pubs.empty:
        logger.warning("Нет данных для построения графа")
        return nx.Graph()

    # Собираем словари author_id -> author_name и author_name -> author_id
    author_names = {}
    author_ids = {}
    with get_connection() as conn:
        df_authors = pd.read_sql("SELECT id, name FROM authors", conn)
        author_names = dict(zip(df_authors['id'], df_authors['name']))
        author_ids = dict(zip(df_authors['name'], df_authors['id']))

    G = nx.Graph()

    for _, pub in df_pubs.iterrows():
        pub_id = pub['id']
        cited_by = pub['cited_by_count']

        authors_in_pub = df_auth[df_auth['publication_id'] == pub_id]['author_id'].tolist()
        author_names_list = [author_names.get(aid, aid) for aid in authors_in_pub if aid in author_names]

        for author_name in author_names_list:
            if author_name not in G:
                G.add_node(author_name,
                           publications=0,
                           total_citations=0,
                           author_id=author_ids.get(author_name, author_name),
                           is_main_author=author_name in MAIN_AUTHORS)
            G.nodes[author_name]['publications'] += 1
            G.nodes[author_name]['total_citations'] += cited_by

        for i, a1 in enumerate(author_names_list):
            for a2 in author_names_list[i + 1:]:
                if G.has_edge(a1, a2):
                    G[a1][a2]['weight'] += 1
                else:
                    G.add_edge(a1, a2, weight=1)

    # Детерминированная раскладка. Считается один раз и сохраняется вместе с
    # графом в дисковом кэше; клиент расставляет узлы по preset (без повторного
    # запуска cose-layout при каждом открытии страницы).
    # Компоненты связности раскладываются отдельно и упаковываются в ряды с
    # зазорами (_layout_by_components) — иначе разные кластеры наезжают друг на
    # друга и граф превращается в «кашу».
    if G.number_of_nodes() == 0:
        return G
    positions = _layout_by_components(G)
    for n in G.nodes():
        G.nodes[n]['pos'] = {'x': float(positions[n][0]), 'y': float(positions[n][1])}

    return G


def _node_radius(G, node):
    """Радиус круга узла (пиксели): совпадает с node_size/2 в cytoscape."""
    pubs = G.nodes[node].get('publications', 1)
    return (20 + min(pubs, 30)) / 2


def _push_apart_overlaps(G, positions, gap_factor=2.5, max_iter=60):
    """Расталкивает пары узлов ближе, чем (r1+r2)*gap_factor. Детерминировано,
    работает в пиксельных координатах. Итерации сходятся быстро — обычно < 10."""
    nodes = list(G.nodes())
    for _ in range(max_iter):
        total_move = 0.0
        for i, a in enumerate(nodes):
            ra = _node_radius(G, a)
            for b in nodes[i + 1:]:
                rb = _node_radius(G, b)
                min_d = (ra + rb) * gap_factor
                dx = positions[b][0] - positions[a][0]
                dy = positions[b][1] - positions[a][1]
                d = math.hypot(dx, dy)
                if 0 < d < min_d:
                    push = (min_d - d) / 2
                    ux, uy = dx / d, dy / d
                    positions[a][0] -= ux * push
                    positions[a][1] -= uy * push
                    positions[b][0] += ux * push
                    positions[b][1] += uy * push
                    total_move += push
        if total_move < 0.05:
            break


def _layout_by_components(G):
    """Раскладывает каждый компонент связности отдельно и упаковывает кластеры
    в ряды (shelf packing) с зазором — компоненты не пересекаются и не образуют
    «кашу». Детерминировано: единый seed у spring_layout + фиксированный порядок
    компонент (по убыванию размера)."""
    comps = sorted((G.subgraph(c).copy() for c in nx.connected_components(G)),
                   key=lambda c: c.number_of_nodes(), reverse=True)
    if not comps:
        return {}

    UNIT = 40.0  # пикселей на условную единицу размера (side = sqrt(nodes))
    boxes = []   # (width, height, node -> [x, y] локальные координаты)

    for comp in comps:
        n = comp.number_of_nodes()
        if n == 1:
            local = {next(iter(comp.nodes())): [0.0, 0.0]}
        else:
            cpos = nx.spring_layout(comp, seed=42, iterations=30, k=7.5 / n ** 0.5)
            xs = [p[0] for p in cpos.values()]
            ys = [p[1] for p in cpos.values()]
            w = (max(xs) - min(xs)) or 1.0
            h = (max(ys) - min(ys)) or 1.0
            side = math.sqrt(n)
            local = {nd: [((x - min(xs)) / w) * side * UNIT,
                          ((y - min(ys)) / h) * side * UNIT]
                     for nd, (x, y) in cpos.items()}

        # Убираем перекрытия узлов внутри компоненты (работает в пикселях)
        _push_apart_overlaps(comp, local)
        xs = [p[0] for p in local.values()]
        ys = [p[1] for p in local.values()]
        minx, miny = min(xs), min(ys)
        for nd in local:
            local[nd] = [local[nd][0] - minx, local[nd][1] - miny]
        xs = [p[0] for p in local.values()]
        ys = [p[1] for p in local.values()]
        boxes.append(((max(xs) - min(xs)) or UNIT,
                      (max(ys) - min(ys)) or UNIT,
                      local))

    # Shelf-упаковка: заполняем ряды слева направо, ширина ряда ~1.6*sqrt(площадей)
    GAP = 120.0  # пикселей между компонентами (больше, чем диаметр узла)
    total_area = sum(w * h for w, h, _ in boxes)
    row_target = math.sqrt(total_area) * 1.6

    positions = {}
    x, y, row_h = 0.0, 0.0, 0.0
    for w, h, local in boxes:
        if x > 0 and x + w > row_target:
            x, y = 0.0, y + row_h + GAP
            row_h = 0.0
        for nd, (px, py) in local.items():
            positions[nd] = (x + px, y + py)
        x += w + GAP
        row_h = max(row_h, h)

    return positions


@lru_cache(maxsize=8)
def _cached_full_graph(key):
    """Полный граф: с диска, либо построение + сохранение. Кэш по ключу БД."""
    G = _load_graph_from_disk(key)
    if G is None:
        G = _build_full_graph()
        _save_graph_to_disk(key, G)
    return G

@timeit
def build_graph(min_pubs=1, min_cites=0):
    key = _db_state_key()
    G = _cached_full_graph(key)

    if min_pubs > 1 or min_cites > 0:
        nodes_to_keep = [
            node for node, data in G.nodes(data=True)
            if data.get('publications', 0) >= min_pubs and data.get('total_citations', 0) >= min_cites
        ]
        G = G.subgraph(nodes_to_keep).copy()

    logger.info(f"Граф построен: {G.number_of_nodes()} узлов, {G.number_of_edges()} рёбер")
    return G

def clear_graph_cache():
    """Сброс кэша графа (память + диск). Вызывается после ETL."""
    _cached_full_graph.cache_clear()
    load_data_from_db.cache_clear()
    try:
        GRAPH_CACHE_FILE.unlink(missing_ok=True)
        logger.info("Дисковый кэш графа удалён")
    except Exception as e:
        logger.warning(f"Не удалось удалить кэш графа: {e}")

def graph_to_cytoscape_elements(G):
    """Преобразует NetworkX-граф в формат для Cytoscape."""
    if G.number_of_nodes() == 0:
        return [], []

    nodes = []
    edges = []

    color_idx = 0
    node_colors = {}

    for node in G.nodes():
        is_main = G.nodes[node].get('is_main_author', False)

        if is_main:
            node_color = '#E74C3C'
        else:
            if node not in node_colors:
                node_colors[node] = COLOR_PALETTE[color_idx % len(COLOR_PALETTE)]
                color_idx += 1
            node_color = node_colors[node]

        pubs = G.nodes[node].get('publications', 1)
        node_size = 20 + min(pubs, 30)
        label = node.split()[-1] if len(node.split()) > 1 else node

        nodes.append({
            'position': G.nodes[node].get('pos', {}),
            'data': {
                'id': node,
                'label': label,
                'full_name': node,
                'author_id': G.nodes[node].get('author_id', node),
                'publications': pubs,
                'citations': G.nodes[node].get('total_citations', 0),
                'is_main_author': is_main,
                'node_size': node_size,
                'color': node_color
            }
        })

    for u, v, data in G.edges(data=True):
        edges.append({
            'data': {
                'source': u,
                'target': v,
                'weight': data.get('weight', 1)
            }
        })

    return nodes, edges

# ===== LAYOUT СТРАНИЦЫ (ТОЛЬКО ОДИН РАЗ!) =====
layout = html.Div([
    html.H1("📊 SPBU Research Collaboration Network", className="text-center mt-4"),
    html.P("Co-authorship network and research topics", className="text-center text-muted mb-4"),
    html.Hr(),
    dbc.Row([
        dbc.Col([
            html.Div([
                html.H5("🔬 Co-authorship Network", className="mb-3"),
                cyto.Cytoscape(
                    id='coauthorship-graph',
                    elements=[],
                    stylesheet=STYLESHEET,
                    layout={
                        'name': 'preset',
                        'fit': True,
                        'padding': 40,
                        'zoom': 1,
                    },
                    style={'width': '100%', 'height': '600px', 'border': '1px solid #e0e0e0', 'borderRadius': '8px'},
                    minZoom=0.2,
                    maxZoom=2,
                    zoomingEnabled=True,
                    userZoomingEnabled=True,
                    panningEnabled=True,
                    userPanningEnabled=True
                ),
                html.Div(
                    html.Small("💡 Click nodes for details | Drag to rearrange", className="text-muted"),
                    className="text-center mt-3"
                )
            ])
        ], width=8),
        dbc.Col([
            html.Div([
                html.H6("🎛️ Filters", className="mb-3"),
                html.Label("Min Publications:", className="small"),
                dcc.Slider(
                    id='min-pubs-slider',
                    min=1, max=20, step=1, value=1,
                    marks={i: str(i) for i in [1, 5, 10, 15, 20]}
                ),
                html.Label("Min Citations:", className="small mt-3"),
                dcc.Slider(
                    id='min-cites-slider',
                    min=0, max=100, step=1, value=0,
                    marks={0: '0', 50: '50', 100: '100'}
                ),
                dbc.Checkbox(
                    id='show-labels',
                    label="Show full names",
                    value=False,
                    className="mt-3"
                ),
                html.Hr(),
                html.Div([
                    html.Span("🔴 ", style={'color': '#E74C3C'}),
                    html.Small("Main SPbSU authors"),
                    html.Br(),
                    html.Span("🟢 ", style={'color': '#4ECDC4'}),
                    html.Small("Collaborators")
                ], className="small")
            ], className="p-3 mb-3",
                style={'backgroundColor': 'white', 'borderRadius': '8px', 'border': '1px solid #e0e0e0'}),
            html.Div([
                html.H6("📌 Author Info", className="mb-3"),
                html.Div(id="node-info", children=[
                    html.Div("Click on any node", className="text-center text-muted py-4")
                ], style={'maxHeight': '350px', 'overflowY': 'auto'})
            ], className="p-3 mb-3",
                style={'backgroundColor': 'white', 'borderRadius': '8px', 'border': '1px solid #e0e0e0'}),
            html.Div([
                html.H6("📊 Statistics", className="mb-3"),
                dbc.Row([
                    dbc.Col(html.Div([
                        html.Div("📚", style={'fontSize': '20px'}),
                        html.Div("0", id='stat-papers', style={'fontSize': '24px', 'fontWeight': 'bold'}),
                        html.Div("Papers", className="small text-muted")
                    ], className="text-center p-2"), width=6),
                    dbc.Col(html.Div([
                        html.Div("👥", style={'fontSize': '20px'}),
                        html.Div("0", id='stat-authors', style={'fontSize': '24px', 'fontWeight': 'bold'}),
                        html.Div("Authors", className="small text-muted")
                    ], className="text-center p-2"), width=6),
                ]),
                dbc.Row([
                    dbc.Col(html.Div([
                        html.Div("🔗", style={'fontSize': '20px'}),
                        html.Div("0", id='stat-edges', style={'fontSize': '24px', 'fontWeight': 'bold'}),
                        html.Div("Connections", className="small text-muted")
                    ], className="text-center p-2"), width=6),
                    dbc.Col(html.Div([
                        html.Div("📖", style={'fontSize': '20px'}),
                        html.Div("0", id='stat-citations', style={'fontSize': '20px', 'fontWeight': 'bold'}),
                        html.Div("Citations", className="small text-muted")
                    ], className="text-center p-2"), width=6),
                ]),
            ], className="p-3",
                style={'backgroundColor': 'white', 'borderRadius': '8px', 'border': '1px solid #e0e0e0'})
        ], width=4)
    ], className="mb-4"),
    # dbc.Row([
    #     dbc.Col([
    #         html.Div([
    #             html.H6("🏷️ Top Research Topics", className="mb-3"),
    #             dcc.Graph(id='topics-chart', config={'displayModeBar': False})
    #         ], className="p-3",
    #             style={'backgroundColor': 'white', 'borderRadius': '8px', 'border': '1px solid #e0e0e0'})
    #     ], width=6),
    #     dbc.Col([
    #         html.Div([
    #             html.H6("📈 Publication Trends", className="mb-3"),
    #             dcc.Graph(id='yearly-trend', config={'displayModeBar': False})
    #         ], className="p-3",
    #             style={'backgroundColor': 'white', 'borderRadius': '8px', 'border': '1px solid #e0e0e0'})
    #     ], width=6),
    # ])
])

# ===== CALLBACKS =====
@callback(
    [Output('coauthorship-graph', 'elements'),
     Output('stat-papers', 'children'),
     Output('stat-authors', 'children'),
     Output('stat-edges', 'children'),
     Output('stat-citations', 'children')],
    [Input('min-pubs-slider', 'value'),
     Input('min-cites-slider', 'value')]
)
@timeit
def update_graph(min_pubs, min_cites):
    logger.info(f"Обновление графа: min_pubs={min_pubs}, min_cites={min_cites}")
    G = build_graph(min_pubs, min_cites)
    nodes, edges = graph_to_cytoscape_elements(G)

    total_papers = sum(G.nodes[node].get('publications', 0) for node in G.nodes())
    total_citations = sum(G.nodes[node].get('total_citations', 0) for node in G.nodes())

    return (
        nodes + edges,
        str(total_papers),
        str(G.number_of_nodes()),
        str(G.number_of_edges()),
        f"{total_citations:,}"
    )

@callback(
    [Output("coauthorship-graph", "stylesheet")],
    [Input("show-labels", "value")]
)
def update_labels(show_labels):
    updated_stylesheet = STYLESHEET.copy()
    for style in updated_stylesheet:
        if style.get('selector') == 'node':
            if show_labels:
                style['style']['label'] = 'data(full_name)'
                style['style']['font-size'] = '8px'
            else:
                style['style']['label'] = 'data(label)'
                style['style']['font-size'] = '10px'
    return [updated_stylesheet]

@callback(
    Output("node-info", "children"),
    Input("coauthorship-graph", "selectedNodeData")
)
def display_node_info(selected_nodes):
    if not selected_nodes:
        return "Click on any node"
    node = selected_nodes[0]
    author_id = node.get('author_id', '')
    return html.Div([
        html.H6(node.get('full_name', 'Unknown')),
        html.Div(f"Publications: {node.get('publications', 0)}"),
        html.Div(f"Citations: {node.get('citations', 0)}"),
        html.Br(),
        dbc.Button("Открыть профиль", color="primary", size="sm",
                   href=f"/author/{author_id}" if author_id else "#"),
    ])

