# ----------------------------
# Imports
# ----------------------------

import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from flask import abort
from flask import request
from sqlalchemy import text 

# ----------------------------
# App and Configuration
# ----------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")

# ----------------------------
# Database setup (SQLite local, Postgres on Render)
# ----------------------------
uri = os.environ.get("DATABASE_URL")
# Render sometimes gives postgres:// — SQLAlchemy expects postgresql+psycopg2://
if uri and uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql+psycopg2://", 1)
basedir = os.path.abspath(os.path.dirname(__file__))
sqlite_path = "sqlite:///" + os.path.join(basedir, "nature_spots.db")
# Fallback to local sqlite if no DATABASE_URL
app.config["SQLALCHEMY_DATABASE_URI"] = uri or sqlite_path
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

with app.app_context():
    db.create_all()

# ----------------------------
# Models
# ----------------------------

class User(db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
# ONE user -> MANY NatureSpot
    spots = db.relationship(
        "NatureSpot",
        back_populates="creator",
        cascade="all, delete-orphan",
        lazy="dynamic",)
# if you have SavedSpot:
    saved = db.relationship("SavedSpot", backref="user", cascade="all, delete-orphan")

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password, raw)

class NatureSpot(db.Model):
    __tablename__ = "nature_spot"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    location = db.Column(db.String(120), nullable=False)
    tags = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.Text, nullable=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    # MATCHES User.spots above
    creator = db.relationship("User", back_populates="spots")

class SavedSpot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    spot_id = db.Column(db.Integer, db.ForeignKey('nature_spot.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'spot_id', name='uq_user_spot'),)


# ----------------------------
# Routes
# ----------------------------

# Home route
@app.route('/')
def home():
    q = request.args.get('q', '').strip()
    tag = request.args.get('tag', '').strip().lower()

    # base query
    query = NatureSpot.query

    # keyword search in title/description/location
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(NatureSpot.title.ilike(like),
                NatureSpot.description.ilike(like),
                NatureSpot.location.ilike(like))
        )

    # simple tag match (tags stored as comma/text)
    if tag:
        query = query.filter(NatureSpot.tags.ilike(f"%{tag}%"))

    spots = query.order_by(NatureSpot.id.desc()).all()

    # saved ids for current user
    saved_ids = set()
    if session.get('user_id'):
        saved_ids = {
            s.spot_id for s in SavedSpot.query
            .filter_by(user_id=session['user_id']).all()
        }

    # collect a lightweight tag cloud (optional)
    all_tags = []
    for s in NatureSpot.query.with_entities(NatureSpot.tags).all():
        if s.tags:
            all_tags.extend([t.strip().lower() for t in s.tags.split(',') if t.strip()])
    # unique + sorted
    all_tags = sorted(set(all_tags))

    return render_template('spots.html',
                           spots=spots,
                           saved_ids=saved_ids,
                           q=q, tag=tag, all_tags=all_tags)

# Register route
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']

        # Check if username or email already exists
        existing_user = User.query.filter(
            (User.username == username) | (User.email == email)
        ).first()
        if existing_user:
            flash('Username or email already exists.', 'error')
            return redirect(url_for('register'))

        # Hash password and save to DB
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)
        new_user = User(username=username, email=email, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()

        flash('Registration successful! Please log in.', 'Success')
        return redirect(url_for('login'))

    return render_template('register.html')

# Login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['username'] = user.username
            flash('Logged in successfully!', 'success')
            return redirect(url_for('home'))
        else:
            flash('Invalid username or password.', 'error')
            return redirect(url_for('login'))

    return render_template('login.html')

# Profile routes
@app.route('/profile')
def my_profile():
    if 'user_id' not in session:
        flash('Please log in to view your profile.', 'warning')
        return redirect(url_for('login'))
    return redirect(url_for('profile', username=session['username']))

@app.route('/profile/<username>')
def profile(username):
    user = User.query.filter_by(username=username).first_or_404()
    user_spots = user.spots
    saved_spots = (
        db.session.query(NatureSpot)
        .join(SavedSpot, SavedSpot.spot_id == NatureSpot.id)
        .filter(SavedSpot.user_id == user.id)
        .order_by(NatureSpot.id.desc())
        .all()
    )
    return render_template('profile.html', user=user, user_spots=user_spots, saved_spots=saved_spots)

# New spots route
@app.route('/spots/new', methods=['GET', 'POST'])
def add_spot():
    if 'user_id' not in session:
        flash('You must be logged in to add a spot.', 'warning')
        return redirect(url_for('login'))

    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        location = request.form['location']
        tags = request.form['tags']
        image_url = request.form['image_url']

        spot = NatureSpot(
            title=title,
            description=description,
            location=location,
            tags=tags,
            image_url=image_url or None,
            user_id=session['user_id']
        )
        db.session.add(spot)
        db.session.commit()
        flash('Nature spot added successfully!', 'success')
        return redirect(url_for('home'))

    return render_template('add_spot.html')

# Inspiration route
@app.route('/inspiration')
def inspiration():
    quotes = [
        "Walk as if you are kissing the Earth with your feet. — Thích Nhất Hạnh",
        "In every walk with nature one receives far more than he seeks. — John Muir",
        "Adopt the pace of nature: her secret is patience. — Ralph Waldo Emerson",
        "The clearest way into the Universe is through a forest wilderness. — John Muir",
        "Between every two pines is a doorway to a new world. — John Muir",
        "Nature does not hurry, yet everything is accomplished. — Lao Tzu",
        "Let yourself be silently drawn by the strange pull of what you really love. — Rumi",
        "The earth has music for those who listen. — (often attributed to Shakespeare)"
    ]

    # A few beautiful Unsplash images (safe to hotlink for a demo)
    images = [
        "https://images.unsplash.com/photo-1501785888041-af3ef285b470?q=80&w=1400&auto=format&fit=crop",
        "https://images.unsplash.com/photo-1441974231531-c6227db76b6e?q=80&w=1400&auto=format&fit=crop",
        "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?q=80&w=1400&auto=format&fit=crop",
        "https://images.unsplash.com/photo-1500534314209-a25ddb2bd429?q=80&w=1400&auto=format&fit=crop",
        "https://images.unsplash.com/photo-1469474968028-56623f02e42e?q=80&w=1400&auto=format&fit=crop",
        "https://images.unsplash.com/photo-1506744038136-46273834b3fb?q=80&w=1400&auto=format&fit=crop",
    ]
    return render_template('inspiration.html', quotes=quotes, images=images)

# Logout route
@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# Edit and Delete routes
@app.route('/spots/<int:spot_id>/edit', methods=['GET', 'POST'])
def edit_spot(spot_id):
    if 'user_id' not in session:
        flash('Please log in.', 'warning')
        return redirect(url_for('login'))

    spot = get_spot_or_404(spot_id)
    if spot.user_id != session['user_id']:
        abort(403)

    if request.method == 'POST':
        spot.title = request.form['title']
        spot.description = request.form['description']
        spot.location = request.form['location']
        spot.tags = request.form['tags']
        image_url = request.form['image_url'].strip()
        spot.image_url = image_url or None
        db.session.commit()
        flash('Spot updated.', 'success')
        return redirect(url_for('profile', username=session['username']))

    return render_template('edit_spot.html', spot=spot)

@app.route('/spots/<int:spot_id>/delete', methods=['POST'])
def delete_spot(spot_id):   # <-- endpoint name MUST be delete_spot
    if 'user_id' not in session:
        flash('Please log in.', 'warning')
        return redirect(url_for('login'))

    spot = get_spot_or_404(spot_id)
    if spot.user_id != session['user_id']:
        abort(403)

    db.session.delete(spot)
    db.session.commit()
    flash('Spot deleted.', 'success')
    return redirect(url_for('profile', username=session['username']))

@app.route('/spots/<int:spot_id>/save', methods=['POST'])
def toggle_save(spot_id):
    if 'user_id' not in session:
        flash('Please log in to save spots.', 'warning')
        return redirect(url_for('login'))

    spot = NatureSpot.query.get_or_404(spot_id)
    existing = SavedSpot.query.filter_by(
        user_id=session['user_id'], spot_id=spot.id
    ).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        flash('Removed from saved.')
    else:
        db.session.add(SavedSpot(user_id=session['user_id'], spot_id=spot.id))
        db.session.commit()
        flash('Saved!')

    return redirect(request.referrer or url_for('home'))

@app.route('/spots/<int:spot_id>')
def spot_detail(spot_id):
    spot = NatureSpot.query.get_or_404(spot_id)

    # is this spot saved by the current user?
    is_saved = False
    if session.get('user_id'):
        is_saved = SavedSpot.query.filter_by(
            user_id=session['user_id'], spot_id=spot.id
        ).first() is not None

    return render_template('spot_detail.html', spot=spot, is_saved=is_saved)

# ----------------------------
# Error Handlers
# ----------------------------
@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html'), 403

def get_spot_or_404(spot_id):
    spot = NatureSpot.query.get(spot_id)
    if not spot:
        abort(404)
    return spot




# Create tables if they don't exist (safe to run every start)
with app.app_context():
    db.create_all()




if __name__ == '__main__':
    app.run(debug=True)