cask "dctl" do
  arch arm: "arm64", intel: "x86_64"

  version "0.4.5"
  sha256 arm:   "45e042ecbdbdb8ea50627394bb23760a5810b2b3b21ee71a2b8257238bafce62",
         intel: "6581d8e9a199cbbb99392f3a2446bf2c9646cd115e98302d18f30c2d913c6273"

  url "https://github.com/vishal-9878/dctl/releases/download/v#{version}/dctl-#{version}-macos-#{arch}.pkg"
  name "dctl"
  desc "Agentless desktop app for Docker and PM2 on many servers, over SSH"
  homepage "https://github.com/vishal-9878/dctl"

  livecheck do
    url :url
    strategy :github_latest
  end

  pkg "dctl-#{version}-macos-#{arch}.pkg"

  uninstall pkgutil: "io.github.vishal-9878.dctl",
            delete:  [
              "/Applications/dctl.app",
              "/usr/local/bin/dctl",
              "/usr/local/bin/dctl-ui",
              "/usr/local/lib/dctl",
            ]

  zap trash: [
    "~/.cache/dctl",
    "~/.config/dctl",
  ]
end
