using System;
using System.Linq;
using Qiongtu.Sandbox;

// Pure local-draft validation. Does not claim server authorization or model connectivity.
public static class SettingsChecks
{
    private static int count;
    private static void Check(bool condition, string message)
    { if (!condition) throw new Exception(message); count++; }
    private static bool Has(SandboxClientSettings value, string field) => SandboxSettingsValidation.Validate(value).Any(issue => issue.field == field);
    public static void Main()
    {
        var settings = new SandboxClientSettings();
        Check(SandboxSettingsValidation.Validate(settings).Count == 0, "defaults must be valid offline");
        Check(!ReferenceEquals(settings.red, settings.blue) && !ReferenceEquals(settings.red.generation, settings.blue.generation), "sides must not share mutable models");
        settings.red.generation.enabled = true;
        Check(Has(settings, "red.generation.baseUrl") && !Has(settings, "blue.generation.baseUrl"), "enabled models need endpoint; blue independent");
        settings.red.generation.baseUrl = "https://models.example/v1";
        settings.red.generation.credentialRef = "RED_AI_API_KEY";
        Check(SandboxSettingsValidation.Validate(settings).Count == 0, "valid reference and endpoint");
        settings.red.generation.credentialRef = "sk-this-is-an-actual-key";
        Check(Has(settings, "red.generation.credentialRef"), "key-like input rejected");
        settings.red.generation.credentialRef = "";
        Check(SandboxSettingsValidation.Validate(settings).Count == 0, "unauthenticated local models supported");
        foreach (var endpoint in new[] { "", "ftp://localhost/v1", "https://user:password@example.com/v1", "https://example.com/v1?api_key=value", "https://example.com/v1#secret", "http://remote.example/v1" })
            Check(SandboxSettingsValidation.EndpointError(endpoint).Length > 0, "invalid endpoint accepted: " + endpoint);
        foreach (var endpoint in new[] { "http://127.0.0.1:8000/api/v1", "http://localhost:11434/v1", "http://[::1]:8000/v1", "https://models.example/v1" })
            Check(SandboxSettingsValidation.EndpointError(endpoint).Length == 0, "valid endpoint rejected: " + endpoint);
        settings.serviceBaseUrl = "https://example.com";
        Check(Has(settings, "serviceBaseUrl"), "service API version required");
        settings.serviceBaseUrl = "https://example.com/api/v1/";
        Check(!Has(settings, "serviceBaseUrl"), "trailing slash permitted");
        settings.serviceTimeoutSeconds = 0;
        Check(Has(settings, "serviceTimeoutSeconds"), "timeout cannot be zero");
        settings.serviceTimeoutSeconds = 10;
        settings.red.runTokenBudget = 100;
        Check(Has(settings, "red.runTokenBudget"), "budget cannot undercut output limit");
        settings.red.runTokenBudget = 20000;
        foreach (var invalid in new[] { float.NaN, float.PositiveInfinity, -0.1f, 2.1f })
        { settings.red.temperature = invalid; Check(Has(settings, "red.temperature"), "invalid temperature"); }
        settings.red.temperature = 0.2f;
        settings.blue.readRetries = 4;
        Check(Has(settings, "blue.readRetries") && !Has(settings, "red.readRetries"), "retry bound and field routing");
        settings.blue.readRetries = 0;
        settings.blue.embedding.baseUrl = "not a url";
        Check(Has(settings, "blue.embedding.baseUrl"), "disabled but nonempty endpoints still validated");
        settings.blue.embedding.baseUrl = "";
        settings.red.profileVersion = "\r\n";
        Check(Has(settings, "red.profileVersion"), "versions must be printable nonempty text");
        settings.red.profileVersion = "v2";
        Check(SandboxSettingsValidation.Validate(settings).Count == 0, "fully valid draft");
        settings.formatVersion = 99;
        Check(Has(settings, "formatVersion"), "unsupported format must not be used");
        Check(Has(null, "formatVersion"), "null record must not crash");
        settings = new SandboxClientSettings { red = null };
        Check(Has(settings, "red"), "missing nested side must not crash");
        Console.WriteLine("本机设置校验通过：" + count + " 项");
    }
}
