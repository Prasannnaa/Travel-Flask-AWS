from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from datetime import datetime
from pymongo import MongoClient
from bson.objectid import ObjectId
import boto3
import uuid

app = Flask(__name__)
app.secret_key = 'secret'

# MongoDB connection
client = MongoClient('mongodb+srv://prasannalakshmivadapalli:889emkkEvQnkGkIW@cluster0.x2nw2wy.mongodb.net/')
db = client['travel_booking']
users_collection = db['users']
bookings_collection = db['bookings']
travel_data_collection = db['travel_data']

# DynamoDB connection
dynamodb = boto3.resource('dynamodb', region_name='ap-south-1')
users_table = dynamodb.Table('travel-users')
bookings_table = dynamodb.Table('Bookings')

# Category-to-layout mapping
layouts = {
    'flights': 'flight',
    'trains': 'train',
    'buses': 'bus',
    'cars': None,
    'hotels': None
}


@app.route("/", methods=["GET"])
def home():
    user = session.get("user")
    destination = request.args.get("destination")
    travel_date = request.args.get("date")

    items = []
    if destination and travel_date:
        destination_lower = destination.lower()
        items = list(travel_data_collection.find({
            "$or": [
                {"route": {"$regex": destination_lower, "$options": "i"}},
                {"location": {"$regex": destination_lower, "$options": "i"}}
            ]
        }))
    return render_template("home.html", user=user, items=items, destination=destination, travel_date=travel_date)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = users_collection.find_one({'username': username, 'password': password})
        if user:
            session['user'] = user['username']
            return redirect(url_for('home'))
        flash("Invalid username or password")
        return render_template("login.html")
    return render_template("login.html")


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if users_collection.find_one({'username': username}):
            flash("Username already taken.")
            return render_template("signup.html")

        users_collection.insert_one({'username': username, 'password': password})

        # Store in DynamoDB
        try:
            users_table.put_item(Item={
                'Email': username,
                'Password': password
            })
        except Exception as e:
            print("Error saving user to DynamoDB:", e)

        flash("Signup successful! Please login.")
        return redirect(url_for('login'))

    return render_template("signup.html")


@app.route('/logout')
def logout():
    session.pop('user', None)
    flash('Logout successful', 'success')
    return redirect(url_for('login'))


@app.route('/<category>')
def show_category(category):
    show_index = request.args.get("show")
    try:
        show_index = int(show_index) if show_index is not None else None
    except ValueError:
        show_index = None
    items = list(travel_data_collection.find({'category': category.lower()}))
    return render_template("category.html", category=category, items=items, show_index=show_index)


@app.route('/book/<category>/<int:index>', methods=['POST'])
def book(category, index):
    if 'user' not in session:
        return redirect(url_for('login'))

    items = list(travel_data_collection.find({'category': category.lower()}))
    if index >= len(items):
        return "Invalid index"

    item = items[index]
    item_id = f"{category}_{index}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    booking_data = {
        "user": session['user'],
        "category": category,
        "index": index,
        "item_id": item_id,
        "item": item,
        "timestamp": timestamp
    }

    if category == "car":
        booking_data["travel_date"] = request.form.get("travel_date")
        booking_data["full_name"] = request.form.get("full_name")
        booking_data["seats"] = []
        booking_data["total_price"] = item['price']

    elif category == "hotel":
        booking_data["full_name"] = request.form.get("full_name")
        booking_data["checkin_date"] = request.form.get("checkin_date")
        booking_data["checkout_date"] = request.form.get("checkout_date")
        booking_data["guests"] = request.form.get("guests")
        booking_data["room_type"] = request.form.get("room_type")
        booking_data["seats"] = []
        booking_data["total_price"] = item['price']

    else:
        selected_seats = request.form.getlist("seat")
        full_name = request.form.get("full_name", "")
        travel_date = request.form.get("travel_date", "")
        seat_count = len(selected_seats)
        price_per_seat = item['price']
        total_price = price_per_seat * seat_count if seat_count > 0 else price_per_seat

        booking_data.update({
            "full_name": full_name,
            "travel_date": travel_date,
            "seats": selected_seats,
            "total_price": total_price
        })

    bookings_collection.insert_one(booking_data)

    # Save to DynamoDB Bookings table
    try:
        booking_id = str(uuid.uuid4())
        dynamo_item = {
            'Email': session['user'],
            'Booking_id': booking_id,
            'Category': category,
            'Timestamp': timestamp,
            'TravelDate': booking_data.get("travel_date", ""),
            'Seats': booking_data.get("seats", []),
            'TotalPrice': str(booking_data.get("total_price", 0)),
            'ItemName': item.get("name", "N/A")
        }
        bookings_table.put_item(Item=dynamo_item)
    except Exception as e:
        print("Error saving booking to DynamoDB:", e)

    return render_template("confirmation.html",
                           item=item,
                           seats=booking_data.get("seats", []),
                           full_name=booking_data.get("full_name", ""),
                           travel_date=booking_data.get("travel_date", ""),
                           total_price=booking_data.get("total_price", 0))


@app.route('/my_bookings')
def my_bookings():
    if 'user' not in session:
        return redirect(url_for('login'))
    bookings = list(bookings_collection.find({'user': session['user']}))
    return render_template("my_bookings.html", bookings=bookings)


@app.route('/cancel/<booking_id>', methods=['POST'])
def cancel_booking(booking_id):
    if 'user' not in session:
        return redirect(url_for('login'))

    password = request.form.get('password')
    user = users_collection.find_one({'username': session['user']})

    if not user or user['password'] != password:
        flash("Incorrect password. Booking not cancelled.")
        return redirect(url_for('my_bookings'))

    bookings_collection.delete_one({'_id': ObjectId(booking_id)})
    flash("Booking cancelled successfully.")
    return redirect(url_for('my_bookings'))


@app.route("/search")
def search():
    destination = request.args.get("destination")
    date = request.args.get("date")

    if not destination or not date:
        flash("Please enter both destination and date.")
        return redirect(url_for("home"))

    items = list(travel_data_collection.find({
        "route": {"$regex": destination, "$options": "i"}
    }))

    return render_template("category.html", category=f"Results for {destination} on {date}", items=items, booked_seats={})


@app.route('/api/booked_seats', methods=['GET'])
def get_booked_seats():
    item_name = request.args.get('item_name')
    category = request.args.get('category')
    travel_date = request.args.get('travel_date')
    bookings = bookings_collection.find({
        "item.name": item_name,
        "category": category,
        "travel_date": travel_date
    })
    booked = []
    for booking in bookings:
        booked.extend(booking.get("seats", []))
    return jsonify({"booked_seats": booked})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

