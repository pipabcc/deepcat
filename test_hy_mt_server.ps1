$ErrorActionPreference = "Stop"

$Headers = @{
  Authorization = "Bearer sk-1234"
}

$Models = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8080/v1/models" `
  -Headers $Headers

Write-Host ("Models: " + (($Models.data | ForEach-Object { $_.id }) -join ", "))

$Body = @{
  model = "hy-mt2-1.8b-q4_k_m"
  messages = @(
    @{
      role = "user"
      content = "Translate the following segment into Chinese, without additional explanation.`n`nHello world"
    }
  )
  temperature = 0.7
  top_p = 0.6
  top_k = 20
  repetition_penalty = 1.05
  repeat_penalty = 1.05
  max_tokens = 4096
} | ConvertTo-Json -Depth 8

$Response = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8080/v1/chat/completions" `
  -Method Post `
  -ContentType "application/json" `
  -Headers $Headers `
  -Body $Body

Write-Host ("Translation: " + $Response.choices[0].message.content)
