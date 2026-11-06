cask "dctl" do
  arch arm: "arm64", intel: "x86_64"

  version "0.4.10"
  sha256 arm:   "e7b76494a37981657bbab00468a9b1cf5c7a18efc90d545af8a30414d0af21fd",
         intel: "2cfdfdae7cff9f5dbfdd301d3a3aba7d266028405ce6f986a8a85a71a14962f8"

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
