param(
    [string]$Root = "C:\Scripts\Assimp\assimp-v6.0.5-isolated",
    [string]$Tag = "v6.0.5"
)

$ErrorActionPreference = "Stop"

$src = Join-Path $Root "src"
$build = Join-Path $Root "build"
$install = Join-Path $Root "install"

New-Item -ItemType Directory -Force -Path $Root | Out-Null

if (-not (Test-Path $src)) {
    git clone --branch $Tag --depth 1 https://github.com/assimp/assimp.git $src
} else {
    git -C $src fetch --tags origin
    git -C $src checkout $Tag
    git -C $src pull --ff-only origin $Tag
}

cmake -S $src -B $build `
    -G "Visual Studio 17 2022" `
    -A x64 `
    -DASSIMP_BUILD_ASSIMP_TOOLS=ON `
    -DASSIMP_BUILD_TESTS=OFF `
    -DASSIMP_NO_EXPORT=OFF `
    -DBUILD_SHARED_LIBS=ON `
    -DCMAKE_INSTALL_PREFIX="$install"

cmake --build $build --config Release --target assimp install

$installedExe = Join-Path $install "bin\assimp.exe"
$builtExe = Join-Path $build "bin\Release\assimp.exe"

if (Test-Path $installedExe) {
    Write-Host "ASSIMP_EXE=$installedExe"
    exit 0
}
if (Test-Path $builtExe) {
    Write-Host "ASSIMP_EXE=$builtExe"
    exit 0
}

throw "assimp.exe not found after build."
