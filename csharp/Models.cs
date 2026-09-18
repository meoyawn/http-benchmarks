using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

public sealed partial record NewPost
{
    public required string Content { get; init; }
    public required string Email { get; init; }
    public bool IsValid() => !string.IsNullOrEmpty(Content) && Email is not null && EmailRule().IsMatch(Email);

    // \z prevents .NET's special treatment of a trailing newline with $.
    [GeneratedRegex(@"\A[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\z", RegexOptions.CultureInvariant)]
    private static partial Regex EmailRule();
}

public sealed record Post(long Id, long UserId, string Content, long CreatedAt, long UpdatedAt);
internal sealed record DatabaseConfiguration(string Version, Dictionary<string, string> Pragmas, string[] CompileOptions);

[JsonSourceGenerationOptions(PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower)]
[JsonSerializable(typeof(NewPost))]
[JsonSerializable(typeof(Post))]
[JsonSerializable(typeof(DatabaseConfiguration))]
internal partial class JsonModel : JsonSerializerContext;
