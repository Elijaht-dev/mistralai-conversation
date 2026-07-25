param(
    [string] $OutputPath = (
        Join-Path $PSScriptRoot (
            '..\custom_components\mistral_conversation\brand\icon.png'
        )
    )
)

Add-Type -AssemblyName System.Drawing

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = [System.IO.Path]::GetDirectoryName($resolvedOutput)
[System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null

$bitmap = [System.Drawing.Bitmap]::new(512, 512)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.Clear([System.Drawing.Color]::Transparent)

function New-RoundedRectanglePath {
    param(
        [float] $X,
        [float] $Y,
        [float] $Width,
        [float] $Height,
        [float] $Radius
    )

    $diameter = $Radius * 2
    $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $path.AddArc($X, $Y, $diameter, $diameter, 180, 90)
    $path.AddArc($X + $Width - $diameter, $Y, $diameter, $diameter, 270, 90)
    $path.AddArc(
        $X + $Width - $diameter,
        $Y + $Height - $diameter,
        $diameter,
        $diameter,
        0,
        90
    )
    $path.AddArc($X, $Y + $Height - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return $path
}

$tilePath = New-RoundedRectanglePath -X 16 -Y 16 -Width 480 -Height 480 -Radius 104
$tileBounds = [System.Drawing.RectangleF]::new(16, 16, 480, 480)
$tileBrush = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
    $tileBounds,
    [System.Drawing.ColorTranslator]::FromHtml('#FFB000'),
    [System.Drawing.ColorTranslator]::FromHtml('#F04424'),
    45
)
$graphics.FillPath($tileBrush, $tilePath)

$bubblePath = New-RoundedRectanglePath -X 80 -Y 88 -Width 352 -Height 288 -Radius 60
$bubblePath.StartFigure()
$bubblePath.AddPolygon(
    [System.Drawing.PointF[]] @(
        [System.Drawing.PointF]::new(132, 348),
        [System.Drawing.PointF]::new(112, 432),
        [System.Drawing.PointF]::new(208, 366)
    )
)
$bubblePath.CloseFigure()
$bubbleBrush = [System.Drawing.SolidBrush]::new(
    [System.Drawing.ColorTranslator]::FromHtml('#FFFDF8')
)
$graphics.FillPath($bubbleBrush, $bubblePath)

# Mistral's official gradient pixel-cat emblem:
# https://mistral.ai/brand/
$markX = 144
$markY = 152
$markUnit = 32
$amberBrush = [System.Drawing.SolidBrush]::new(
    [System.Drawing.ColorTranslator]::FromHtml('#FFAF01')
)
$orangeBrush = [System.Drawing.SolidBrush]::new(
    [System.Drawing.ColorTranslator]::FromHtml('#FF8204')
)
$vermillionBrush = [System.Drawing.SolidBrush]::new(
    [System.Drawing.ColorTranslator]::FromHtml('#FA500F')
)
$redBrush = [System.Drawing.SolidBrush]::new(
    [System.Drawing.ColorTranslator]::FromHtml('#E61300')
)
$burgundyBrush = [System.Drawing.SolidBrush]::new(
    [System.Drawing.ColorTranslator]::FromHtml('#C4001D')
)

$graphics.FillRectangle(
    $amberBrush,
    $markX + $markUnit,
    $markY,
    $markUnit,
    $markUnit
)
$graphics.FillRectangle(
    $amberBrush,
    $markX + (5 * $markUnit),
    $markY,
    $markUnit,
    $markUnit
)
$graphics.FillRectangle(
    $orangeBrush,
    $markX + $markUnit,
    $markY + $markUnit,
    2 * $markUnit,
    $markUnit
)
$graphics.FillRectangle(
    $orangeBrush,
    $markX + (4 * $markUnit),
    $markY + $markUnit,
    2 * $markUnit,
    $markUnit
)
$graphics.FillRectangle(
    $vermillionBrush,
    $markX + $markUnit,
    $markY + (2 * $markUnit),
    5 * $markUnit,
    $markUnit
)
$graphics.FillRectangle(
    $redBrush,
    $markX + $markUnit,
    $markY + (3 * $markUnit),
    $markUnit,
    $markUnit
)
$graphics.FillRectangle(
    $redBrush,
    $markX + (3 * $markUnit),
    $markY + (3 * $markUnit),
    $markUnit,
    $markUnit
)
$graphics.FillRectangle(
    $redBrush,
    $markX + (5 * $markUnit),
    $markY + (3 * $markUnit),
    $markUnit,
    $markUnit
)
$graphics.FillRectangle(
    $burgundyBrush,
    $markX,
    $markY + (4 * $markUnit),
    3 * $markUnit,
    $markUnit
)
$graphics.FillRectangle(
    $burgundyBrush,
    $markX + (4 * $markUnit),
    $markY + (4 * $markUnit),
    3 * $markUnit,
    $markUnit
)

$bitmap.Save($resolvedOutput, [System.Drawing.Imaging.ImageFormat]::Png)

$burgundyBrush.Dispose()
$redBrush.Dispose()
$vermillionBrush.Dispose()
$orangeBrush.Dispose()
$amberBrush.Dispose()
$bubbleBrush.Dispose()
$bubblePath.Dispose()
$tileBrush.Dispose()
$tilePath.Dispose()
$graphics.Dispose()
$bitmap.Dispose()

$repositoryBrandPath = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\brand\icon.png')
)
if ($resolvedOutput -ne $repositoryBrandPath) {
    [System.IO.Directory]::CreateDirectory(
        [System.IO.Path]::GetDirectoryName($repositoryBrandPath)
    ) | Out-Null
    Copy-Item -LiteralPath $resolvedOutput -Destination $repositoryBrandPath -Force
}

Write-Output $resolvedOutput
