# FANUC FTP File Transfer

## Target

- Physical controller: `10.50.160.51`
- FTP access is anonymous.
- FTP root is controller `MD:`, not USB device `UD1:`.
- This V9.30 controller requires native `.TP` binaries because `R796` ASCII
  Program Loader is not installed.

## Required procedure

1. Confirm the target controller and create a backup.
2. List controller files before changing anything:

   ```powershell
   $ip = "10.50.160.51"
   $listing = curl.exe --silent --show-error --fail `
     --list-only "ftp://$ip/"
   $listing
   ```

3. Back up an existing program before replacing it:

   ```powershell
   $name = "HOME"
   $remote = $name.ToLowerInvariant() + ".tp"
   curl.exe --silent --show-error --fail `
     "ftp://$ip/$remote" --output "$env:TEMP\$name.TP"
   ```

4. FANUC does not reliably overwrite a loaded TP program. If the program
   exists, delete it before uploading:

   ```powershell
   curl.exe --silent --show-error --fail `
     --quote "DELE $remote" "ftp://$ip/"
   Start-Sleep -Milliseconds 500
   ```

5. Upload the replacement:

   ```powershell
   $source = "C:\path\HOME.TP"
   curl.exe --silent --show-error --fail `
     --upload-file $source "ftp://$ip/$remote"
   ```

6. Download the loaded file and verify its SHA-256 hash:

   ```powershell
   $verify = "$env:TEMP\VERIFY_$name.TP"
   curl.exe --silent --show-error --fail `
     "ftp://$ip/$remote" --output $verify

   $sourceHash = (Get-FileHash $source -Algorithm SHA256).Hash
   $robotHash = (Get-FileHash $verify -Algorithm SHA256).Hash
   if ($sourceHash -ne $robotHash) {
       throw "Robot verification failed: $name"
   }
   ```

## FTP 550 or “program already exists”

Do not repeatedly retry. Complete these checks:

1. Abort all executing tasks.
2. Select a different TP program.
3. If it is a Background Logic program, stop it and remove it from its BG
   Logic slot.
4. Confirm the program is not protected.
5. Delete the existing `.TP`, wait briefly, then upload again.
6. Retrieve `ERRALL.LS` and inspect the newest FANUC alarm:

   ```powershell
   curl.exe --silent --show-error --fail `
     "ftp://$ip/errall.ls" --output "$env:TEMP\errall.ls"
   ```

Renaming the uploaded file is not a workaround because the FANUC program name
is embedded in the TP binary.

## Restrictions

- Never replace a commissioned customizable program without explicit approval.
- Never delete a program without first backing it up.
- Do not upload `.SV`, `.VR`, `.IO`, DCS, mastering, or system files through
  FTP without explicit authorization and a controller-specific restore plan.
- Report every successful, skipped, and failed filename.
