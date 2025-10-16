GTFSWeaver — Guia do Usuário
*****************************

.. contents::
   :depth: 2
   :local:
   :backlinks: none

Introdução
==========
O **GTFSWeaver** é uma biblioteca Python 3.10+ que permite construir *feeds* GTFS
a partir de dados mínimos de rotas e frequências.  
A versão atual (denominada **Opção A**) substitui o modelo anterior do
``make_gtfs`` por uma estrutura simplificada de entrada baseada em três arquivos principais:

- ``meta.csv`` – metadados da agência e período de validade;
- ``lines.geojson`` – geometrias das rotas;
- ``timetable.csv`` – tabela consolidada com horários e headways.

Arquivos opcionais permitem o refinamento do *feed*:

- ``stops.csv`` – pontos de parada (padrão GTFS);
- ``speed_zones.geojson`` – zonas com velocidades médias específicas.

Este guia descreve como preencher esses arquivos, quais campos são obrigatórios
e como migrar dados de versões anteriores do ``make_gtfs``.

------------------------------------------------------------

1. Estrutura Esperada dos Arquivos
==================================

.. list-table::
   :header-rows: 1
   :widths: 25 10 45 20

   * - Arquivo
     - Obrigatório
     - Função
     - Formato
   * - ``meta.csv``
     - ✅
     - Metadados da agência e validade do *feed*
     - CSV
   * - ``lines.geojson``
     - ✅
     - Geometrias (um ``LineString`` por rota)
     - GeoJSON
   * - ``timetable.csv``
     - ✅
     - Rotas, dias, horários e *headways*
     - CSV
   * - ``stops.csv``
     - ⚙️
     - Lista de paradas (GTFS padrão)
     - CSV
   * - ``speed_zones.geojson``
     - ⚙️
     - Zonas com velocidades médias
     - GeoJSON

------------------------------------------------------------

2. Descrição Detalhada dos Arquivos
===================================

``meta.csv``
------------
Metadados do *feed* e da agência responsável.

.. list-table::
   :header-rows: 1
   :widths: 25 10 45 20

   * - Coluna
     - Obrigatória
     - Descrição
     - Exemplo
   * - ``agency_name``
     - ✅
     - Nome da agência
     - City Transit
   * - ``agency_url``
     - ✅
     - URL completa da agência
     - https://citytransit.br
   * - ``agency_timezone``
     - ✅
     - Fuso horário Olson
     - America/Sao_Paulo
   * - ``start_date``
     - ✅
     - Data inicial de validade (YYYYMMDD)
     - 20250101
   * - ``end_date``
     - ✅
     - Data final de validade (YYYYMMDD)
     - 20251231

``lines.geojson``
-----------------
Arquivo GeoJSON contendo uma *FeatureCollection* de ``LineStrings``.
Cada *feature* representa o trajeto de uma rota e **deve incluir a propriedade** ``shape_id``.

Boas práticas:

- Coordenadas no sistema WGS84 (EPSG 4326);
- Um ``LineString`` por rota;
- Evite múltiplos loops – use ``direction = 2`` no ``timetable.csv`` se houver
  operação nos dois sentidos.

``timetable.csv``
-----------------
Tabela consolidada principal. Cada linha representa **uma combinação
de rota, faixa horária e conjunto de dias**.

.. list-table::
   :header-rows: 1
   :widths: 25 10 45 20

   * - Coluna
     - Obrigatória
     - Descrição
     - Exemplo
   * - ``route_id``
     - ✅
     - Identificador único da rota
     - R10
   * - ``route_short_name``
     - ✅
     - Nome curto da linha
     - 10
   * - ``route_long_name``
     - ⚙️
     - Nome descritivo
     - Centro – Campus UFRJ
   * - ``route_type``
     - ✅
     - Tipo GTFS (inteiro)
     - 3
   * - ``shape_id``
     - ✅
     - Referência à geometria
     - R10_main
   * - ``direction``
     - ⚙️
     - 0 = reverso; 1 = direto; 2 = ambos (padrão)
     - 2
   * - ``dow``
     - ✅
     - Máscara de dias (``MTWTF``, ``SS``, ``MTWTFSS``)
     - MTWTF
   * - ``start_time``
     - ✅
     - Início da faixa horária (HH:MM:SS)
     - 06:00:00
   * - ``end_time``
     - ✅
     - Fim da faixa horária (HH:MM:SS)
     - 09:00:00
   * - ``headway_min``
     - ✅
     - Intervalo entre partidas (minutos)
     - 10
   * - ``speed_kph``
     - ⚙️
     - Velocidade média (km/h)
     - 24

``stops.csv`` (opcional)
------------------------
Segue o formato do GTFS ``stops.txt``:

.. code-block:: csv

   stop_id,stop_name,stop_lat,stop_lon
   C01,Terminal Central,-22.9012,-43.1756
   C02,Campus UFRJ,-22.8600,-43.2300

Se omitido, o GTFSWeaver gera automaticamente dois pontos por ``shape_id``
(início e fim), removendo duplicatas.

``speed_zones.geojson`` (opcional)
----------------------------------
Arquivo de polígonos em WGS84 com propriedades:

- ``speed_zone_id`` – identificador único;
- ``route_type`` – tipo de rota (inteiro GTFS);
- ``speed`` – velocidade média (km/h) para rotas dentro da zona.

------------------------------------------------------------

3. Regras de Validação
======================

- ``shape_id`` deve existir em ``lines.geojson``;
- ``dow`` deve seguir formato válido (``MTWTF``, ``SS`` etc.);
- ``start_time`` < ``end_time``;
- ``headway_min`` > 0 e ≤ 360;
- ``direction`` ∈ {0, 1, 2} (padrão 2);
- ``speed_kph`` preenche-se com valores padrão se ausente.

------------------------------------------------------------

4. Exemplo de Preenchimento
===========================

``meta.csv``
------------
.. code-block:: csv

   agency_name,agency_url,agency_timezone,start_date,end_date
   Cidade Transporte,https://cidtransporte.br,America/Sao_Paulo,20250101,20251231

``timetable.csv``
-----------------
.. code-block:: csv

   route_id,route_short_name,route_long_name,route_type,shape_id,direction,dow,start_time,end_time,headway_min,speed_kph
   R10,10,Centro–Campus,3,R10_main,2,MTWTF,06:00:00,09:00:00,10,24
   R10,10,Centro–Campus,3,R10_main,2,MTWTF,09:00:00,16:00:00,15,
   R10,10,Centro–Campus,3,R10_main,2,SS,08:00:00,18:00:00,20,22
   R51,51X,Aeroporto Express,3,R51_exp,1,MTWTFSS,06:00:00,22:00:00,12,35

------------------------------------------------------------

5. Migração a partir do *make_gtfs*
===================================

.. list-table::
   :header-rows: 1
   :widths: 30 30 40

   * - Arquivo antigo
     - Campo
     - Novo destino (Opção A)
   * - ``frequencies.csv``
     - ``route_short_name``
     - ``route_short_name``
   * - 
     - ``route_long_name``
     - ``route_long_name``
   * - 
     - ``route_type``
     - ``route_type``
   * - 
     - ``shape_id``
     - ``shape_id``
   * - 
     - ``direction``
     - ``direction`` (padrão 2 se vazio)
   * - 
     - ``frequency`` (veículos/h)
     - Converter para ``headway_min = round(60 / frequency)``
   * - ``service_windows.csv``
     - ``start_time`` / ``end_time``
     - ``start_time`` / ``end_time``
   * - 
     - Flags de dias (0/1)
     - Combinar em ``dow`` (ex. ``MTWTFSS``)
   * - ``shapes.geojson``
     - ``feature.properties.shape_id``
     - ``shape_id`` (inalterado)
   * - ``meta.csv``
     - Todos os campos
     - ``meta.csv`` (inalterado)
   * - *(opcional)* ``frequencies.csv``
     - ``speed``
     - ``speed_kph``

------------------------------------------------------------

6. Boas Práticas
================

1. **Consistência de IDs** — o mesmo ``shape_id`` deve aparecer em todos os arquivos.
2. **Faixas horárias claras** — use blocos de 2 a 4 horas.
3. **Máscaras de dias padronizadas** — ``MTWTF`` = Seg–Sex; ``SS`` = Sáb–Dom.
4. **Velocidades médias realistas** — ônibus 22–28 km/h, trem 40–60 km/h, metrô 60–80 km/h.
5. **Direção 2** — para rotas bidirecionais no mesmo traçado.

------------------------------------------------------------

7. Execução e Validação
=======================

Após preencher os arquivos, execute:

.. code-block:: bash

   uv run gtfs-weaver build ./meu_projeto/

O comando:
1. Lê e valida os arquivos;
2. Gera as tabelas GTFS (``routes.txt``, ``trips.txt``, ``stop_times.txt``, etc.);
3. Produz o arquivo ``gtfsfile.zip``.

Verifique o resultado com o validador oficial:

.. code-block:: bash

   feedvalidator gtfsfile.zip

------------------------------------------------------------

8. Resumo Visual
================

.. code-block:: text

   meta.csv          →  agency.txt
   lines.geojson     →  shapes.txt
   timetable.csv     →  routes.txt, trips.txt, stop_times.txt, calendar.txt
   stops.csv (opt.)  →  stops.txt
   speed_zones.geojson (opt.) → speed overrides

------------------------------------------------------------

9. Contato e Suporte
====================

- Repositório: https://github.com/your-user/GTFSWeaver  
- Canal de dúvidas: Issues → *data-entry-help*  
- Responsável técnico: José B.  

------------------------------------------------------------

Anexos — Modelos de Arquivos
============================

**``examples/meta_template.csv``**

.. code-block:: csv

   agency_name,agency_url,agency_timezone,start_date,end_date
   Minha Agência,https://minhaagencia.br,America/Sao_Paulo,20250101,20251231

**``examples/timetable_template.csv``**

.. code-block:: csv

   route_id,route_short_name,route_long_name,route_type,shape_id,direction,dow,start_time,end_time,headway_min,speed_kph
   R01,01,Centro–Bairro,3,R01_main,2,MTWTF,06:00:00,22:00:00,12,25

**``examples/stops_template.csv``**

.. code-block:: csv

   stop_id,stop_name,stop_lat,stop_lon
   S01,Terminal Central,-22.90,-43.17
   S02,Bairro Novo,-22.93,-43.20
