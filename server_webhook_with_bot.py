# server_webhook_with_bot.py
from flask import Flask, request, jsonify
import requests
import threading
import time
import re
import json
import os
from datetime import datetime, timedelta
import discord
from discord.ext import commands, tasks

# ====== CONFIGURAÇÕES ======
# IMPORTANTE: Cole as URLs COMPLETAS dos webhooks do Discord
# Exemplo: https://discord.com/api/webhooks/1234567890/AbCdEfGhIjKlMnOpQrStUvWxYz
WEBHOOK_A1 = "https://discord.com/api/webhooks/1432234723123658865/O6MnGqtpNjn8Z8ItJaQolO_A0VFBrL8Xf9BHSQ5XrlXlK2-uTOdzOSY---tQBxwdAU6F"  # 1M–10M
WEBHOOK_A2 = "https://discord.com/api/webhooks/1432234671609090170/aeFdcxLY2IsnjZJcrWXRVUw3OlogV5sI06UJ7tMWgIggKZhpk7cj0vKRy5GJsvB8cw5r"  # 1M–10M alternado
WEBHOOK_B  = "https://discord.com/api/webhooks/1421519342025048064/NA2PvYR6j-bjIgF6PenKqz7WYdidnobyl_xyFqZMTTeK8mlPGSmtf0hK7hBOeRE4nCZb"  # 10M–100M
WEBHOOK_C  = "https://discord.com/api/webhooks/1430372825772064821/H-08cjnKafbsrxJtXYonGG-MiBIhQDzNUgkw89O5gqjIFp_NejK_wDJAlwnR-aWkO30J"  # >100M

BOT_TOKEN = "MTQyMjAzNjQyNDMyNDI4ODUzMw.G8qy7L.G4iSMH5bA06AtuOx8ya_yJaftNMMlmlI0a14iM"
STATS_CHANNEL_ID = 1432178785146503221  # ID do canal onde enviar estatísticas

PLACE_ID = 109983668079237
CACHE_FILE = "cache.json"

SEND_INTERVAL = 30  # 1 hora
RESET_INTERVAL = 300  # 24h

app = Flask(__name__)

# ====== Bot Discord ======
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ====== estado ======
name_counter = {}
job_history = []  # Histórico de jobs com secrets
last_reset = datetime.now()
_state = {"use_first_webhook": True, "stats_message_id": None}

MAX_HISTORY = 50  # Máximo de jobs no histórico

# ====== FUNÇÕES AUXILIARES ======
def parse_generation(gen_str: str) -> float:
    """Converte strings como $1.5M/s em número absoluto."""
    if not gen_str:
        return 0.0
    s = str(gen_str).upper().replace("$", "").replace("/S", "").strip()
    m = re.search(r"([\d\.]+)\s*([KMB]?)", s)
    if not m:
        nums = re.findall(r"[\d\.]+", s)
        return float(nums[0]) if nums else 0.0
    val, suf = m.groups()
    try:
        v = float(val)
    except:
        return 0.0
    if suf == "K":
        v *= 1_000
    elif suf == "M":
        v *= 1_000_000
    elif suf == "B":
        v *= 1_000_000_000
    return v

def make_joiner_url(place_id: int, job_id: str) -> str:
    return f"https://chillihub1.github.io/chillihub-joiner/?placeId={place_id}&gameInstanceId={job_id}"

def make_teleport_script(place_id: int, job_id: str) -> str:
    return (
        f"local TeleportService = game:GetService('TeleportService')\n"
        f"local Players = game:GetService('Players')\n"
        f"TeleportService:TeleportToPlaceInstance({place_id}, '{job_id}', Players.LocalPlayer)"
    )

def build_embed_payload(name, generation, rarity, job_id):
    join_url = make_joiner_url(PLACE_ID, job_id)
    teleport_script = make_teleport_script(PLACE_ID, job_id)
    embed = {
        "title": "🐱 Novo Secret Encontrado!",
        "color": 16753920,
        "fields": [
            {"name": "Name", "value": f"```{name or 'Unknown'}```", "inline": True},
            {"name": "Generation", "value": f"```{generation or '0'}```", "inline": True},
            {"name": "Rarity", "value": f"```{rarity or 'Unknown'}```", "inline": True},
            {"name": "JOB ID", "value": f"```{job_id}```", "inline": False},
            {"name": "Join Link", "value": f"[**Entrar**]({join_url})", "inline": False},
            {"name": "Teleport Script", "value": f"```lua\n{teleport_script}\n```", "inline": False},
        ],
        "footer": {"text": f"Detectado em {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}
    }
    components = [
        {"type": 1, "components":[{"type": 2,"style":5,"label":"Entrar","url":join_url}]}
    ]
    return {"embeds":[embed], "components":components}

def send_to_webhook(name, generation, rarity, job_id):
    gen_value = parse_generation(generation)
    webhook_url = None

    global _state
    if 1_000_000 < gen_value <= 10_000_000:
        webhook_url = WEBHOOK_A1 if _state.get("use_first_webhook", True) else WEBHOOK_A2
        _state["use_first_webhook"] = not _state.get("use_first_webhook", True)
        save_state()
    elif 10_000_000 < gen_value <= 100_000_000:
        webhook_url = WEBHOOK_B
    elif gen_value > 100_000_000:
        webhook_url = WEBHOOK_C
    else:
        # menor que 1M, não envia
        return

    payload = build_embed_payload(name, generation, rarity, job_id)
    try:
        r = requests.post(webhook_url, json=payload, timeout=8)
        if not r.ok:
            print(f"[ERRO WEBHOOK] {r.status_code} {r.text}")
        else:
            print(f"[OK] enviado webhook para {name} (gen {generation})")
    except Exception as e:
        print("[ERRO request.post]", e)

# ====== cache ======
def load_cache():
    global name_counter, last_reset, _state, job_history
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                name_counter.update(data.get("names", {}))
                job_history.extend(data.get("job_history", []))
                if "last_reset" in data:
                    last_reset = datetime.fromisoformat(data["last_reset"])
                if "use_first_webhook" in data:
                    _state["use_first_webhook"] = data["use_first_webhook"]
                if "stats_message_id" in data:
                    _state["stats_message_id"] = data["stats_message_id"]
            print(f"[CACHE] carregado {len(name_counter)} nomes, {len(job_history)} jobs")
        except Exception as e:
            print("[WARN] falha ao carregar cache:", e)

def save_state():
    try:
        to_save = {
            "names": name_counter,
            "job_history": job_history,
            "last_reset": last_reset.isoformat(),
            "use_first_webhook": _state["use_first_webhook"],
            "stats_message_id": _state.get("stats_message_id")
        }
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2)
    except Exception as e:
        print("[WARN] falha ao salvar cache:", e)

def reset_cache():
    global name_counter, last_reset, job_history
    name_counter.clear()
    job_history.clear()
    last_reset = datetime.now()
    save_state()
    print("[CACHE] resetado 24h")

# ====== ESTATÍSTICAS BOT ======
def build_stats_embed():
    """Cria embed com estatísticas dos secrets encontrados."""
    if not name_counter:
        embed = discord.Embed(
            title="📊 Estatísticas de Secrets",
            description="Nenhum secret encontrado ainda neste período.",
            color=discord.Color.blue()
        )
        return embed
    
    # Ordenar por quantidade
    sorted_secrets = sorted(name_counter.items(), key=lambda x: x[1], reverse=True)
    
    total = sum(name_counter.values())
    
    embed = discord.Embed(
        title="📊 Estatísticas de Secrets - Últimas 24h",
        description=f"**Total de secrets encontrados:** `{total}`\n**Jobs únicos rastreados:** `{len(job_history)}`",
        color=discord.Color.gold(),
        timestamp=datetime.now()
    )
    
    # Top 10 secrets
    top_10 = sorted_secrets[:10]
    if top_10:
        field_value = "\n".join([f"`{i+1}.` **{name}** - `{count}x`" for i, (name, count) in enumerate(top_10)])
        embed.add_field(name="🏆 Top 10 Secrets Mais Encontrados", value=field_value, inline=False)
    
    # Últimos 5 jobs
    if job_history:
        recent_jobs = job_history[:5]
        jobs_text = []
        for job in recent_jobs:
            jobs_text.append(
                f"**{job['name']}** `{job['generation']}` - <t:{int(datetime.strptime(job['timestamp'], '%Y-%m-%d %H:%M:%S').timestamp())}:R>"
            )
        embed.add_field(name="🕐 Últimos 5 Encontrados", value="\n".join(jobs_text), inline=False)
    
    # Informações adicionais
    embed.add_field(name="📈 Tipos Únicos", value=f"`{len(name_counter)}`", inline=True)
    embed.add_field(name="🔄 Próximo Reset", value=f"<t:{int((last_reset + timedelta(seconds=RESET_INTERVAL)).timestamp())}:R>", inline=True)
    
    embed.set_footer(text="Acesse /jobs para ver histórico completo • Atualizado")
    
    return embed

@bot.event
async def on_ready():
    print(f"[BOT] Conectado como {bot.user}")
    print(f"[BOT] ID do canal configurado: {STATS_CHANNEL_ID}")
    
    # Testa se consegue acessar o canal
    channel = bot.get_channel(STATS_CHANNEL_ID)
    if channel:
        print(f"[BOT] Canal encontrado: #{channel.name}")
        try:
            # Envia mensagem inicial de estatísticas
            embed = build_stats_embed()
            msg = await channel.send(embed=embed)
            _state["stats_message_id"] = msg.id
            save_state()
            print(f"[BOT] Mensagem de stats criada (ID: {msg.id})")
        except Exception as e:
            print(f"[ERRO BOT] Não foi possível enviar mensagem: {e}")
    else:
        print(f"[ERRO BOT] Canal {STATS_CHANNEL_ID} não encontrado! Verifique o ID.")
    
    # Inicia loop de estatísticas
    if not send_stats.is_running():
        send_stats.start()

@bot.command(name="stats")
async def manual_stats(ctx):
    """Comando para ver estatísticas manualmente."""
    embed = build_stats_embed()
    await ctx.send(embed=embed)

@bot.command(name="reset")
@commands.has_permissions(administrator=True)
async def manual_reset(ctx):
    """Comando para resetar estatísticas manualmente (apenas admins)."""
    reset_cache()
    await ctx.send("✅ Estatísticas resetadas com sucesso!")

@tasks.loop(minutes=5)
async def send_stats():
    """Atualiza estatísticas a cada 5 minutos."""
    try:
        channel = bot.get_channel(STATS_CHANNEL_ID)
        if not channel:
            print(f"[ERRO BOT] Canal {STATS_CHANNEL_ID} não encontrado!")
            return
        
        embed = build_stats_embed()
        
        # Tenta editar mensagem existente
        message_id = _state.get("stats_message_id")
        if message_id:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(embed=embed)
                print(f"[BOT] ✓ Estatísticas atualizadas (editadas)")
                print(f"[BOT] Total de secrets: {sum(name_counter.values())}")
                return
            except discord.NotFound:
                print(f"[BOT] Mensagem antiga não encontrada, criando nova...")
            except Exception as e:
                print(f"[ERRO BOT] Falha ao editar: {e}")
        
        # Se não conseguiu editar, cria nova mensagem
        msg = await channel.send(embed=embed)
        _state["stats_message_id"] = msg.id
        save_state()
        print(f"[BOT] ✓ Nova mensagem de estatísticas criada (ID: {msg.id})")
        print(f"[BOT] Total de secrets: {sum(name_counter.values())}")
        
    except Exception as e:
        print(f"[ERRO BOT] Falha ao enviar stats: {e}")
        import traceback
        traceback.print_exc()

@send_stats.before_loop
async def before_send_stats():
    await bot.wait_until_ready()
    print("[BOT] Loop de estatísticas pronto para iniciar")

# ====== endpoint ======
@app.route("/api", methods=["POST"])
def receive_api():
    try:
        data = request.json or {}
        name = data.get("Name") or data.get("name")
        generation = data.get("Generation") or data.get("generation")
        job_id = data.get("JobId") or data.get("jobId") or data.get("job_id")
        rarity = data.get("Rarity") or data.get("rarity") or "Unknown"

        if not all([name, generation, job_id]):
            return jsonify({"error":"Campos faltando"}),400

        send_to_webhook(name, generation, rarity, job_id)

        # contar
        name_counter[name] = name_counter.get(name,0)+1
        
        # Adicionar ao histórico de jobs
        job_entry = {
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "name": name,
            "generation": generation,
            "rarity": rarity,
            "placeId": str(PLACE_ID),
            "jobId": job_id
        }
        
        # Adiciona no início da lista
        job_history.insert(0, job_entry)
        
        # Mantém apenas os últimos MAX_HISTORY
        if len(job_history) > MAX_HISTORY:
            job_history.pop()
        
        save_state()
        return jsonify({"status":"OK"}),200
    except Exception as e:
        print("[ERRO API]", e)
        return jsonify({"error":str(e)}),500

@app.route("/jobs", methods=["GET"])
def get_jobs():
    """Retorna histórico de jobs em JSON."""
    return jsonify(job_history)

@app.route("/", methods=["GET"])
def index():
    """Página inicial com informações."""
    return jsonify({
        "status": "online",
        "total_secrets": sum(name_counter.values()),
        "unique_secrets": len(name_counter),
        "recent_jobs": len(job_history),
        "endpoints": {
            "/api": "POST - Enviar secret",
            "/jobs": "GET - Listar jobs recentes",
            "/": "GET - Ver estatísticas"
        }
    })

# ====== loop 24h ======
def reset_loop():
    global last_reset
    while True:
        time.sleep(60)
        if (datetime.now() - last_reset).total_seconds() >= RESET_INTERVAL:
            reset_cache()

# ====== Flask em thread separada ======
def run_flask():
    app.run(host="0.0.0.0", port=8080)

if __name__ == "__main__":
    load_cache()
    
    # Inicia Flask em thread separada
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Inicia loop de reset
    reset_thread = threading.Thread(target=reset_loop, daemon=True)
    reset_thread.start()
    
    # Inicia bot Discord
    print("[INICIANDO] Bot Discord...")
    bot.run(BOT_TOKEN)
