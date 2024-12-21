var app = WebApplication.CreateBuilder(args).Build();
app.MapPost("/echo", (NewPost post) => post);
app.Run();

struct NewPost
{
    public string Content { get; set; }
    public string Email { get; set; }
};