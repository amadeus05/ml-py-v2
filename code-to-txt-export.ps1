# collect-code.ps1
$OutputFile = "singlefile-code.txt"
$ProjectRoot = Get-Location

Write-Host "Collecting all Python project files (excluding venv, __pycache__, etc.)..." -ForegroundColor Green

# Remove old file if it exists
if (Test-Path $OutputFile) { 
    Remove-Item $OutputFile 
    Write-Host "Removed old file $OutputFile" -ForegroundColor Yellow
}

# Create header
$header = @"
================================================================================
  PROJECT FULL CODE COLLECTION
  DATE: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
  EXCLUDED: venv, .venv, __pycache__, .git
================================================================================

"@
$header | Out-File $OutputFile -Encoding UTF8

# Get all .py files recursively, excluding common Python environments/caches
$files = Get-ChildItem -Path "." -Recurse -Filter "*.py" | 
         Where-Object { $_.FullName -notmatch '\\venv\\' -and $_.FullName -notmatch '\\.venv\\' -and $_.FullName -notmatch '\\__pycache__\\' -and $_.FullName -notmatch '\\.git\\' }

$fileCount = 0
foreach ($file in $files) {
    $fileCount++
    $relativePath = $file.FullName.Replace($ProjectRoot, "").TrimStart('\').Replace('\', '/')
    
    Write-Host "Processing: $relativePath" -ForegroundColor Cyan
    
    # Add separator
    $separator = @"

========================================================================
  FILE: $relativePath
========================================================================

"@
    $separator | Out-File $OutputFile -Append -Encoding UTF8
    
    # Add file content
    Get-Content $file.FullName -Encoding UTF8 | Out-File $OutputFile -Append -Encoding UTF8
    
    # Add empty line after file
    "" | Out-File $OutputFile -Append -Encoding UTF8
}

# Add footer
$footer = @"

================================================================================
  END OF FILES
  Total files processed: $fileCount
  Excluded directories: venv, .venv, __pycache__, .git
================================================================================
"@
$footer | Out-File $OutputFile -Append -Encoding UTF8

Write-Host "Done!" -ForegroundColor Green
Write-Host "File created: $OutputFile" -ForegroundColor Yellow
Write-Host "Total files processed: $fileCount" -ForegroundColor Cyan

# Open file?
Write-Host "Open file? (y/n)" -ForegroundColor White
$response = Read-Host
if ($response -eq 'y') {
    Invoke-Item $OutputFile
}