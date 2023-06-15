from flask import Flask, redirect, url_for, render_template, request, session, flash
from datetime import timedelta
from flask_sqlalchemy import SQLAlchemy

#Criar instâcia do flask
app = Flask(__name__)
app.secret_key = "hello"
#Configuração de database, para armazenar as informações passadas pelos usuários
#o users abaixo referencia o nome da tabela
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.sqlite3'
#remove os avisos que ficam aparecendo
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
#Garante o tempo que a pessoa fica sem precisar fazer o login
app.permanent_session_lifetime = timedelta(hours=3)

db = SQLAlchemy(app)

class users(db.Model):
    _id = db.Column("id", db.Integer, primary_key=True)
    name = db.Column("name", db.String(100))
    frente = db.Column("frente",db.String(100))

    def __init__(self, name, frente):
        self.name = name
        self.frente = frente

#Mapear a rota
@app.route("/home")
@app.route("/")
def home():
    return render_template("home.html")


@app.route("/index")
def index():
    return render_template("index.html")

@app.route("/roda_unico")
def roda_unico():
    return render_template("roda_unico.html")

#Aqui é definido o HTTP POST ou GET (também garante as sessões)
#Sessões garantem que ninguém acessa certas páginas sem estar logado
@app.route("/login", methods=["POST", "GET"])
def login():
    if request.method == "POST":
        session.permanent = True
        user = request.form["nm"]
        session["user"] = user
        flash("Login feito com sucesso!")
        return redirect(url_for("user"))
    else:
        if "user" in session:
            flash("Você já está logado.")
            return redirect(url_for("user"))
        return render_template("login.html")

# Aqui é como pego o retorno do usuário que fez o login, garantindo que ele tenha feito o login
@app.route("/user", methods=["POST", "GET"])
def user():
    frente = None
    if "user" in session:
        user = session["user"]

        if request.method=="POST":
            frente = request.form["frente"]
            session["frente"] = frente
            flash("A frente que você atua foi salva!")
        else:
            if "frente" in session:
                frente = session["frente"]

        return render_template("user.html", frente=frente)
    else:
        flash("Você não efetuou o login ainda!")
        return redirect(url_for("login"))

#remove os dados da session (login), será necessário fazer login novamente depois
@app.route("/logout")
def logout():
    flash("O logout foi feito com sucesso! Volte sempre!", "info")
    session.pop("user", None)
    session.pop("frente", None)
    return redirect(url_for("login"))
    


if __name__ == "__main__":
    db.create_all()
    app.run(debug=True)
    
