defmodule CachedConn do
  @enforce_keys [:conn, :cache]
  defstruct [:conn, :cache]

  @type t :: %__MODULE__{
          conn: Exqlite.Sqlite3.db(),
          cache: :ets.table()
        }

  alias Exqlite.Sqlite3

  @spec prepare(t(), String.t()) :: {:error, Sqlite3.reason()} | {:ok, Sqlite3.statement()}
  def prepare(cc, sql) do
    case :ets.lookup(cc.cache, sql) do
      [] ->
        case Sqlite3.prepare(cc.conn, sql) do
          {:ok, stmt} ->
            :ets.insert(cc.cache, {sql, stmt})
            {:ok, stmt}

          {:error, reason} ->
            {:error, reason}
        end

      [{_sql, stmt}] ->
        {:ok, stmt}
    end
  end
end

defmodule DbWriter do
  use GenServer

  alias Exqlite.Sqlite3

  @open_pragmas """
  PRAGMA journal_mode = wal;
  PRAGMA synchronous = normal;
  PRAGMA foreign_keys = on;
  PRAGMA busy_timeout = 10000;

  PRAGMA strict = ON;

  PRAGMA optimize = 0x10002;
  """

  @spec start_link(String.t()) :: :ignore | {:error, any()} | {:ok, pid()}
  def start_link(path) do
    GenServer.start_link(__MODULE__, path, name: __MODULE__)
  end

  @impl true
  @spec init(path :: String.t()) :: {:ok, CachedConn.t()} | {:stop, Sqlite3.reason()}
  def init(path) do
    with {:ok, conn} <- Sqlite3.open(path, mode: [:readwrite, :nomutex]),
         :ok <- Sqlite3.execute(conn, @open_pragmas) do
      cache = :ets.new(:db_cache, [:set, :private])
      {:ok, %CachedConn{conn: conn, cache: cache}}
    else
      {:error, reason} -> {:stop, reason}
    end
  end

  def execute(conn, stmt, args \\ []) do
    if args != [] do
      :ok = Sqlite3.bind(stmt, args)
    end

    ret = Sqlite3.step(conn, stmt)
    :ok = Sqlite3.reset(stmt)
    ret
  end

  def prepared(state, sql, args \\ []) do
    {:ok, stmt} = CachedConn.prepare(state, sql)
    execute(state.conn, stmt, args)
  end

  @impl true
  @spec handle_call(
          request :: HttpPost.new_post(),
          from :: GenServer.from(),
          state :: CachedConn.t()
        ) ::
          {:reply, HttpPost.post(), CachedConn.t()}
  def handle_call(%{"content" => content, "email" => email}, _from, state) do
    :done = prepared(state, "BEGIN IMMEDIATE TRANSACTION")

    :done = prepared(state, "INSERT OR IGNORE INTO users (email) VALUES (?)", [email])

    {:row, [id, user_id, content, created_at, updated_at]} =
      prepared(
        state,
        """
        INSERT INTO posts   (content,   user_id)
        SELECT              ?,          id
        FROM        users
        WHERE       email = ?
        RETURNING   id, user_id, content, created_at, updated_at
        """,
        [content, email]
      )

    :done = prepared(state, "COMMIT")

    {:reply,
     %{
       id: id,
       user_id: user_id,
       content: content,
       created_at: created_at,
       updated_at: updated_at
     }, state}
  end

  @impl true
  @spec terminate(Sqlite3.reason(), Sqlite3.db()) ::
          :ok | {:error, Sqlite3.reason()}
  def terminate(_reason, state) do
    IO.puts("Closing db")

    with :ok <- Sqlite3.execute(state, "PRAGMA optimize"),
         :ok <- Sqlite3.close(state) do
      :ok
    else
      {:error, reason} -> {:error, reason}
    end
  end
end

defmodule HttpPost do
  import Plug.Conn

  @rules %{
    "email" => [
      required: true,
      type: :string,
      regex: ~r/^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/
    ],
    "content" => [
      required: true,
      type: :string,
      regex: ~r/^.+$/
    ]
  }

  # in reality keys are strings, not atoms
  @typedoc """
  %{
    "email" => String.t(),
    "content" => String.t()
  }
  """
  @type new_post() :: %{
          email: String.t(),
          content: String.t()
        }

  @type post() :: %{
          id: integer,
          user_id: integer,
          content: String.t(),
          created_at: integer,
          updated_at: integer
        }

  @spec on_valid(Plug.Conn.t(), data :: new_post()) :: Plug.Conn.t()
  defp on_valid(conn, data) do
    post = GenServer.call(DbWriter, data)

    conn
    |> put_resp_header("content-type", "application/json")
    |> send_resp(201, :json.encode(post))
  end

  @spec http_post(Plug.Conn.t()) :: Plug.Conn.t()
  def http_post(conn) do
    case Validate.validate(conn.body_params, @rules) do
      {:ok, data} ->
        on_valid(conn, data)

      {:error, errors} ->
        conn
        |> put_resp_header("content-type", "application/json")
        |> send_resp(400, :json.encode(%{errors: errors |> Enum.map(&Map.from_struct(&1))}))
    end
  end
end

defmodule Router do
  use Plug.Router

  plug(:match)

  plug(Plug.Parsers,
    parsers: [:json],
    json_decoder: {:json, :decode, []}
  )

  plug(:dispatch)

  post "/posts" do
    HttpPost.http_post(conn)
  end

  # no validation
  post "/echo" do
    conn
    |> put_resp_header("content-type", "application/json")
    |> send_resp(200, :json.encode(conn.body_params))
  end

  match _ do
    send_resp(conn, 404, "oops")
  end
end

defmodule ElixirBandit.Application do
  # See https://hexdocs.pm/elixir/Application.html
  # for more information on OTP Applications
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      {Bandit, plug: Router, ip: {:local, "/tmp/benchmark.sock"}, port: 0},
      {DbWriter, "../db/db.sqlite"}
    ]

    # See https://hexdocs.pm/elixir/Supervisor.html
    # for other strategies and supported options
    opts = [strategy: :one_for_one, name: ElixirBandit.Supervisor]
    Supervisor.start_link(children, opts)
  end
end
