"""Add adg_classes and aircraft_types tables, migrate aircraft.size to adg_class, parking_spots.accepted_sizes to accepted_classes

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-27
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── ADG classes reference table ──────────────────────────────────
    op.create_table(
        "adg_classes",
        sa.Column("class", sa.Integer(), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("wingspan_min_m", sa.Float(), nullable=False),
        sa.Column("wingspan_max_m", sa.Float(), nullable=False),
        sa.Column("tail_height_min_m", sa.Float(), nullable=False),
        sa.Column("tail_height_max_m", sa.Float(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint("class BETWEEN 1 AND 6", name="ck_adg_class_range"),
    )

    op.execute(
        """
        INSERT INTO adg_classes (class, label, wingspan_min_m, wingspan_max_m, tail_height_min_m, tail_height_max_m, description) VALUES
        (1, 'ADG-I',   0,  15,  0,    6.1,  'Light jets, turboprops'),
        (2, 'ADG-II',  15, 24,  6.1,  9.1,  'Mid-size jets'),
        (3, 'ADG-III', 24, 36,  9.1,  13.7, 'Large cabin jets'),
        (4, 'ADG-IV',  36, 52,  13.7, 18.3, 'Narrow-body airliners, BBJs'),
        (5, 'ADG-V',   52, 65,  18.3, 20.1, 'Wide-body aircraft'),
        (6, 'ADG-VI',  65, 80,  20.1, 24.4, 'Super heavy (A380 class)')
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_adg_classes_updated_at
        BEFORE UPDATE ON adg_classes
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )

    # ── Aircraft types table ─────────────────────────────────────────
    op.create_table(
        "aircraft_types",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), primary_key=True),
        sa.Column("make", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("designator", sa.Text()),
        sa.Column("wingspan_m", sa.Float(), nullable=False),
        sa.Column("length_m", sa.Float()),
        sa.Column("adg_class", sa.Integer(), sa.ForeignKey("adg_classes.class"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("make", "model", name="uq_aircraft_types_make_model"),
    )

    op.execute(
        """
        CREATE TRIGGER trg_aircraft_types_updated_at
        BEFORE UPDATE ON aircraft_types
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )

    # Seed aircraft types
    op.execute(
        """
        INSERT INTO aircraft_types (make, model, designator, wingspan_m, adg_class) VALUES
        -- ADG I (<15m)
        ('Cirrus',      'SF50 Vision Jet', 'SF50', 11.7, 1),
        ('Eclipse',     'Eclipse 550',     'EA50', 11.4, 1),
        ('Honda',       'HondaJet',        'HDJT', 12.1, 1),
        ('Embraer',     'Phenom 100',      'PH10', 12.3, 1),
        ('Cessna',      'Citation Mustang', 'C510', 13.2, 1),
        ('Cessna',      'Citation M2',     'C525', 14.3, 1),
        ('Bombardier',  'Learjet 45',      'LJ45', 14.6, 1),
        ('Bombardier',  'Learjet 75',      'LJ75', 14.6, 1),
        -- ADG II (15-24m)
        ('Cessna',      'Citation CJ3+',   'C25B', 15.9, 2),
        ('Cessna',      'Citation CJ4',    'C25C', 15.5, 2),
        ('Pilatus',     'PC-12',           'PC12', 16.2, 2),
        ('Hawker',      'Hawker 800XP',    'H25B', 16.6, 2),
        ('Embraer',     'Phenom 300E',     'E55P', 17.1, 2),
        ('Pilatus',     'PC-24',           'PC24', 17.0, 2),
        ('Beechcraft',  'King Air 250',    'BE25', 17.6, 2),
        ('Beechcraft',  'King Air 350',    'B350', 17.7, 2),
        ('Gulfstream',  'G280',            'G280', 19.0, 2),
        ('Dassault',    'Falcon 2000LXS',  'F2TH', 19.3, 2),
        ('Cessna',      'Citation Sovereign+', 'C680', 19.3, 2),
        ('Dassault',    'Falcon 900LX',    'F900', 19.3, 2),
        ('Cessna',      'Citation X+',     'C750', 19.4, 2),
        ('Bombardier',  'Challenger 650',  'CL60', 19.6, 2),
        ('Embraer',     'Legacy 500',      'E545', 20.3, 2),
        ('Embraer',     'Praetor 500',     'E54P', 20.3, 2),
        ('Bombardier',  'Challenger 350',  'CL35', 21.0, 2),
        ('Embraer',     'Legacy 600',      'E135', 21.2, 2),
        ('Embraer',     'Praetor 600',     'E550', 21.5, 2),
        ('Cessna',      'Citation Latitude', 'C68A', 22.0, 2),
        ('Cessna',      'Citation Longitude', 'C700', 22.5, 2),
        -- ADG III (24-36m)
        ('Gulfstream',  'G450',            'GLF4', 23.7, 3),
        ('Dassault',    'Falcon 6X',       'F6X0', 25.9, 3),
        ('Dassault',    'Falcon 7X',       'FA7X', 26.2, 3),
        ('Gulfstream',  'G500',            'GA5C', 26.3, 3),
        ('Dassault',    'Falcon 8X',       'FA8X', 26.3, 3),
        ('Gulfstream',  'G600',            'GA6C', 28.4, 3),
        ('Gulfstream',  'G550',            'GLF5', 28.5, 3),
        ('Bombardier',  'Global 6000',     'GL6T', 28.6, 3),
        ('Bombardier',  'Global 6500',     'GL65', 28.6, 3),
        ('Embraer',     'Lineage 1000',    'E190', 28.7, 3),
        ('Gulfstream',  'G650',            'GLF6', 30.4, 3),
        ('Gulfstream',  'G650ER',          'GLF6', 30.4, 3),
        ('Gulfstream',  'G700',            'GLF7', 31.4, 3),
        ('Bombardier',  'Global 7500',     'GL7T', 31.4, 3),
        ('Gulfstream',  'G800',            'GL8T', 31.4, 3),
        ('Bombardier',  'Global 8000',     'GL8T', 31.4, 3),
        ('Dassault',    'Falcon 10X',      'F10X', 33.6, 3),
        -- ADG IV (36-52m)
        ('Airbus',      'ACJ TwoTwenty',   'BCS3', 35.1, 4),
        ('Airbus',      'ACJ319neo',       'A19N', 35.8, 4),
        ('Boeing',      'BBJ 737-700',     'B737', 35.8, 4),
        ('Airbus',      'ACJ320neo',       'A20N', 35.8, 4),
        ('Boeing',      'BBJ 737-800',     'B738', 35.8, 4),
        ('Boeing',      'BBJ MAX 7',       'B37M', 35.9, 4),
        ('Boeing',      'BBJ MAX 8',       'B38M', 35.9, 4)
        """
    )

    # ── Alter aircraft table: size (text) → adg_class (integer) ──────
    op.drop_constraint("ck_aircraft_size", "aircraft", type_="check")
    op.add_column("aircraft", sa.Column("adg_class", sa.Integer(), sa.ForeignKey("adg_classes.class"), nullable=True))

    # Migrate existing data
    op.execute("""
        UPDATE aircraft SET adg_class = CASE
            WHEN size = 'S'  THEN 1
            WHEN size = 'M'  THEN 2
            WHEN size = 'L'  THEN 3
            WHEN size = 'XL' THEN 4
            ELSE 2
        END
    """)

    op.alter_column("aircraft", "adg_class", nullable=False)
    op.drop_column("aircraft", "size")

    # ── Alter parking_spots: accepted_sizes (text[]) → accepted_classes (int[]) ──
    op.add_column(
        "parking_spots",
        sa.Column("accepted_classes", sa.ARRAY(sa.Integer()), nullable=True),
    )

    # Migrate: map text sizes to integer classes
    op.execute("""
        UPDATE parking_spots SET accepted_classes = ARRAY(
            SELECT CASE
                WHEN s = 'S'  THEN 1
                WHEN s = 'M'  THEN 2
                WHEN s = 'L'  THEN 3
                WHEN s = 'XL' THEN 4
                ELSE 2
            END
            FROM unnest(accepted_sizes) AS s
        )
    """)

    # Set default for new rows and make non-null
    op.execute("ALTER TABLE parking_spots ALTER COLUMN accepted_classes SET DEFAULT ARRAY[1,2,3,4,5,6]")
    op.alter_column("parking_spots", "accepted_classes", nullable=False)
    op.drop_column("parking_spots", "accepted_sizes")


def downgrade() -> None:
    # Restore parking_spots.accepted_sizes
    op.add_column("parking_spots", sa.Column("accepted_sizes", sa.ARRAY(sa.Text()), nullable=True))
    op.execute("""
        UPDATE parking_spots SET accepted_sizes = ARRAY(
            SELECT CASE
                WHEN c = 1 THEN 'S'
                WHEN c = 2 THEN 'M'
                WHEN c = 3 THEN 'L'
                WHEN c = 4 THEN 'XL'
                ELSE 'M'
            END
            FROM unnest(accepted_classes) AS c
        )
    """)
    op.execute("ALTER TABLE parking_spots ALTER COLUMN accepted_sizes SET DEFAULT ARRAY['S','M','L','XL']")
    op.alter_column("parking_spots", "accepted_sizes", nullable=False)
    op.drop_column("parking_spots", "accepted_classes")

    # Restore aircraft.size
    op.add_column("aircraft", sa.Column("size", sa.String(2), nullable=True))
    op.execute("""
        UPDATE aircraft SET size = CASE
            WHEN adg_class = 1 THEN 'S'
            WHEN adg_class = 2 THEN 'M'
            WHEN adg_class = 3 THEN 'L'
            WHEN adg_class = 4 THEN 'XL'
            ELSE 'M'
        END
    """)
    op.alter_column("aircraft", "size", nullable=False)
    op.execute("ALTER TABLE aircraft ADD CONSTRAINT ck_aircraft_size CHECK (size IN ('S','M','L','XL'))")
    op.drop_column("aircraft", "adg_class")

    # Drop aircraft_types
    op.execute("DROP TRIGGER IF EXISTS trg_aircraft_types_updated_at ON aircraft_types")
    op.drop_table("aircraft_types")

    # Drop adg_classes
    op.execute("DROP TRIGGER IF EXISTS trg_adg_classes_updated_at ON adg_classes")
    op.drop_table("adg_classes")
