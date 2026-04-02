"""Expand aircraft_types to 200+ models, add tail_height_m and length_m where missing

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-30
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add tail_height_m column to aircraft_types
    op.add_column("aircraft_types", sa.Column("tail_height_m", sa.Float(), nullable=True))

    # Backfill length_m for existing types that have NULL
    op.execute("""
        UPDATE aircraft_types SET length_m = wingspan_m * 0.85 WHERE length_m IS NULL
    """)

    # Backfill tail_height_m from ADG class ranges (midpoint)
    op.execute("""
        UPDATE aircraft_types at
        SET tail_height_m = (ac.tail_height_min_m + ac.tail_height_max_m) / 2
        FROM adg_classes ac
        WHERE at.adg_class = ac.class AND at.tail_height_m IS NULL
    """)

    # ── Insert many more aircraft types ──────────────────────────────
    # Format: (make, model, designator, wingspan_m, length_m, tail_height_m, adg_class)

    op.execute("""
        INSERT INTO aircraft_types (make, model, designator, wingspan_m, length_m, tail_height_m, adg_class) VALUES
        -- ═══ ADG I (<15m) - Additional light jets & turboprops ═══
        ('Cessna',      'Citation I',         'C500', 14.4, 13.3, 4.4, 1),
        ('Cessna',      'CitationJet CJ1',    'C525', 14.3, 12.9, 4.2, 1),
        ('Cessna',      'Citation Ultra',      'C560', 15.9, 14.9, 4.6, 1),
        ('Beechcraft',  'King Air 90',         'BE9L', 15.3, 10.8, 4.3, 1),
        ('Beechcraft',  'King Air 100',        'BE10', 14.0, 12.2, 4.6, 1),
        ('Beechcraft',  'Baron 58',            'BE58', 11.5, 9.1, 2.8, 1),
        ('Beechcraft',  'Bonanza G36',         'BE36', 10.2, 8.4, 2.6, 1),
        ('Piper',       'PA-46 Meridian',      'PA46', 13.1, 9.0, 3.4, 1),
        ('Piper',       'PA-31 Navajo',        'PA31', 12.4, 10.6, 4.0, 1),
        ('Piper',       'PA-34 Seneca',        'PA34', 11.9, 8.7, 3.0, 1),
        ('Daher',       'TBM 960',             'TBM9', 12.8, 10.7, 4.4, 1),
        ('Daher',       'TBM 940',             'TBM9', 12.8, 10.7, 4.4, 1),
        ('Daher',       'TBM 850',             'TBM8', 12.7, 10.6, 4.4, 1),
        ('Diamond',     'DA62',                'DA62', 14.3, 9.5, 2.4, 1),
        ('Diamond',     'DA42 Twin Star',      'DA42', 13.4, 8.6, 2.5, 1),
        ('Cessna',      'Caravan 208',         'C208', 15.9, 11.5, 4.3, 1),
        ('Cessna',      'Grand Caravan 208B',  'C208', 15.9, 12.7, 4.7, 1),
        ('Epic',        'E1000 GX',            'EPIC', 13.0, 10.9, 3.7, 1),
        ('SyberJet',    'SJ30i',               'SJ30', 12.9, 14.3, 4.2, 1),
        ('Nextant',     '400XTi',              'BE40', 13.3, 14.7, 4.2, 1),
        ('Cirrus',      'SR22T',               'SR22', 11.7, 7.9, 2.7, 1),

        -- ═══ ADG II (15-24m) - Additional mid-size jets ═══
        ('Cessna',      'Citation Bravo',      'C550', 15.9, 14.4, 4.6, 2),
        ('Cessna',      'Citation Excel',      'C56X', 17.1, 15.8, 5.2, 2),
        ('Cessna',      'Citation Encore',     'C560', 16.0, 14.9, 4.6, 2),
        ('Cessna',      'Citation V',          'C560', 15.9, 14.9, 4.6, 2),
        ('Cessna',      'Citation VII',        'C650', 16.3, 16.0, 5.1, 2),
        ('Hawker',      'Hawker 400XP',        'H25A', 13.4, 14.7, 4.2, 2),
        ('Hawker',      'Hawker 750',          'H25B', 16.6, 15.6, 5.4, 2),
        ('Hawker',      'Hawker 900XP',        'H25B', 16.6, 15.6, 5.6, 2),
        ('Hawker',      'Hawker 4000',         'HA4T', 18.8, 21.1, 5.7, 2),
        ('Bombardier',  'Learjet 35',          'LJ35', 12.0, 14.8, 3.7, 2),
        ('Bombardier',  'Learjet 55',          'LJ55', 13.4, 16.8, 4.5, 2),
        ('Bombardier',  'Learjet 60',          'LJ60', 13.4, 17.9, 4.5, 2),
        ('Bombardier',  'Learjet 70',          'LJ70', 14.6, 17.7, 4.5, 2),
        ('Embraer',     'Legacy 450',          'E545', 20.3, 19.7, 6.4, 2),
        ('Dassault',    'Falcon 50',           'FA50', 18.9, 18.5, 6.3, 2),
        ('Dassault',    'Falcon 10',           'FA10', 13.1, 13.9, 4.6, 2),
        ('Dassault',    'Falcon 20',           'FA20', 16.3, 17.2, 5.3, 2),
        ('Gulfstream',  'G100',                'ASTR', 16.9, 16.9, 5.5, 2),
        ('Gulfstream',  'G150',                'G150', 16.9, 17.3, 5.8, 2),
        ('Gulfstream',  'G200',                'G200', 17.7, 18.9, 6.5, 2),
        ('IAI',         'Westwind 1124',       'WW24', 13.7, 15.9, 4.8, 2),
        ('Mitsubishi',  'MU-2B',               'MU2B', 11.9, 12.0, 4.2, 2),
        ('Piaggio',     'P.180 Avanti EVO',    'P180', 14.0, 14.4, 3.9, 2),
        ('HondaJet',    'HondaJet Elite II',   'HDJT', 12.1, 13.0, 4.5, 2),
        ('Textron',     'Citation Ascend',     'C56X', 17.2, 16.5, 5.3, 2),

        -- ═══ ADG III (24-36m) - Additional large cabin jets ═══
        ('Gulfstream',  'GIV-SP',              'GLF4', 23.7, 26.9, 7.4, 3),
        ('Gulfstream',  'GIII',                'GLF3', 23.7, 25.3, 7.4, 3),
        ('Gulfstream',  'GII',                 'GLF2', 20.9, 24.4, 7.5, 3),
        ('Bombardier',  'Global Express',      'GLEX', 28.6, 30.3, 7.9, 3),
        ('Bombardier',  'Global 5000',         'GL5T', 28.6, 29.5, 7.9, 3),
        ('Bombardier',  'Global 5500',         'GL5T', 28.6, 29.5, 7.9, 3),
        ('Bombardier',  'Challenger 601',      'CL60', 19.6, 20.9, 6.3, 3),
        ('Bombardier',  'Challenger 604',      'CL60', 19.6, 20.9, 6.4, 3),
        ('Bombardier',  'Challenger 605',      'CL60', 19.6, 20.9, 6.4, 3),
        ('Bombardier',  'Challenger 850',      'CRJ2', 21.2, 26.8, 6.2, 3),
        ('Dassault',    'Falcon 900',          'F900', 19.3, 20.2, 7.6, 3),
        ('Dassault',    'Falcon 900EX',        'F900', 19.3, 20.2, 7.6, 3),
        ('Embraer',     'Legacy 650',          'E135', 21.2, 26.3, 6.8, 3),
        ('Embraer',     'ERJ-135',             'E135', 20.0, 26.3, 6.8, 3),
        ('Embraer',     'ERJ-145',             'E145', 20.0, 29.9, 6.8, 3),
        ('Sukhoi',      'Superjet 100',        'SU95', 27.8, 29.9, 10.3, 3),
        ('Embraer',     'E175',                'E170', 28.7, 31.7, 9.7, 3),

        -- ═══ ADG IV (36-52m) - Narrow-body airliners ═══
        ('Boeing',      '737-700',             'B737', 35.8, 33.6, 12.6, 4),
        ('Boeing',      '737-800',             'B738', 35.8, 39.5, 12.6, 4),
        ('Boeing',      '737-900ER',           'B739', 35.8, 42.1, 12.6, 4),
        ('Boeing',      '737 MAX 8',           'B38M', 35.9, 39.5, 12.3, 4),
        ('Boeing',      '737 MAX 9',           'B39M', 35.9, 42.2, 12.3, 4),
        ('Boeing',      '737 MAX 10',          'B3XM', 35.9, 43.8, 12.3, 4),
        ('Boeing',      '757-200',             'B752', 38.1, 47.3, 13.6, 4),
        ('Boeing',      '757-300',             'B753', 38.1, 54.4, 13.6, 4),
        ('Airbus',      'A319',                'A319', 35.8, 33.8, 11.8, 4),
        ('Airbus',      'A320',                'A320', 35.8, 37.6, 11.8, 4),
        ('Airbus',      'A320neo',             'A20N', 35.8, 37.6, 11.8, 4),
        ('Airbus',      'A321',                'A321', 35.8, 44.5, 11.8, 4),
        ('Airbus',      'A321neo',             'A21N', 35.8, 44.5, 11.8, 4),
        ('Airbus',      'A321XLR',             'A21N', 35.8, 44.5, 11.8, 4),
        ('Embraer',     'E190',                'E190', 28.7, 36.2, 10.6, 4),
        ('Embraer',     'E195-E2',             'E295', 35.1, 41.5, 11.5, 4),
        ('Bombardier',  'CRJ-700',             'CRJ7', 23.2, 32.5, 7.6, 4),
        ('Bombardier',  'CRJ-900',             'CRJ9', 24.9, 36.4, 7.6, 4),
        ('COMAC',       'C919',                'C919', 35.8, 38.9, 12.3, 4),
        ('Irkut',       'MC-21',               'MC21', 35.9, 42.3, 11.5, 4),
        ('ATR',         'ATR 42-600',          'AT45', 24.6, 22.7, 7.6, 4),
        ('ATR',         'ATR 72-600',          'AT76', 27.1, 27.2, 7.6, 4),
        ('De Havilland','Dash 8-400',          'DH8D', 28.4, 32.8, 8.3, 4),

        -- ═══ ADG V (52-65m) - Wide-body aircraft ═══
        ('Boeing',      '767-200ER',           'B762', 47.6, 48.5, 15.8, 5),
        ('Boeing',      '767-300ER',           'B763', 47.6, 54.9, 15.8, 5),
        ('Boeing',      '767-400ER',           'B764', 51.9, 61.4, 16.8, 5),
        ('Boeing',      '787-8 Dreamliner',    'B788', 60.1, 56.7, 17.0, 5),
        ('Boeing',      '787-9 Dreamliner',    'B789', 60.1, 62.8, 17.0, 5),
        ('Boeing',      '787-10 Dreamliner',   'B78X', 60.1, 68.3, 17.0, 5),
        ('Boeing',      '777-200ER',           'B772', 60.9, 63.7, 18.5, 5),
        ('Boeing',      '777-200LR',           'B77L', 64.8, 63.7, 18.6, 5),
        ('Boeing',      '777-300ER',           'B77W', 64.8, 73.9, 18.5, 5),
        ('Boeing',      '777X-9',              'B779', 64.8, 76.7, 19.5, 5),
        ('Boeing',      '747-400',             'B744', 64.4, 70.7, 19.3, 5),
        ('Boeing',      '747-8',               'B748', 68.4, 76.3, 19.4, 5),
        ('Airbus',      'A300-600',            'A306', 44.8, 54.1, 16.6, 5),
        ('Airbus',      'A310',                'A310', 43.9, 46.7, 15.8, 5),
        ('Airbus',      'A330-200',            'A332', 60.3, 58.8, 17.4, 5),
        ('Airbus',      'A330-300',            'A333', 60.3, 63.7, 17.4, 5),
        ('Airbus',      'A330-900neo',         'A339', 64.0, 63.7, 17.4, 5),
        ('Airbus',      'A340-300',            'A343', 60.3, 63.7, 17.0, 5),
        ('Airbus',      'A340-600',            'A346', 63.4, 75.4, 17.3, 5),
        ('Airbus',      'A350-900',            'A359', 64.7, 66.8, 17.1, 5),
        ('Airbus',      'A350-1000',           'A35K', 64.7, 73.8, 17.1, 5),
        ('Lockheed',    'C-130J Hercules',     'C130', 40.4, 29.8, 11.8, 5),
        ('Boeing',      'C-17 Globemaster',    'C17',  51.7, 53.0, 16.8, 5),
        ('Ilyushin',    'IL-76',               'IL76', 50.5, 46.6, 14.8, 5),

        -- ═══ ADG VI (65-80m) - Super heavy aircraft ═══
        ('Airbus',      'A380-800',            'A388', 79.8, 73.0, 24.1, 6),
        ('Antonov',     'An-124 Ruslan',       'A124', 73.3, 69.1, 20.8, 6),
        ('Antonov',     'An-225 Mriya',        'A225', 88.4, 84.0, 18.2, 6),
        ('Boeing',      '747-8F Freighter',    'B748', 68.4, 76.3, 19.4, 6),
        ('Lockheed',    'C-5M Galaxy',         'C5',   67.9, 75.3, 19.8, 6)

        ON CONFLICT (make, model) DO NOTHING
    """)


def downgrade() -> None:
    # Remove tail_height_m column
    op.drop_column("aircraft_types", "tail_height_m")

    # Remove newly added aircraft types (keep originals from migration 0003)
    op.execute("""
        DELETE FROM aircraft_types
        WHERE (make, model) NOT IN (
            SELECT make, model FROM aircraft_types
            ORDER BY created_at ASC
            LIMIT 43
        )
    """)
